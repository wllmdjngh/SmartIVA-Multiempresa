import base64
import json
import logging
import re
import urllib.request

from markupsafe import Markup, escape

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_IMG_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.webp'}
_TAM_MIN = 1 * 1024          # 1 KB — por debajo, improbable que tenga contenido real (antes 5KB rechazaba demos reales de ~3.5KB)
_TAM_MAX = 10 * 1024 * 1024  # 10 MB — por encima, satura el OCR


class VeComprobanteInbox(models.Model):
    _name = 've.comprobante.inbox'
    _description = 'Buzón de Comprobantes por Email'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'fecha_email desc, id desc'
    _rec_name = 'email_asunto'

    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    canal_origen = fields.Selection([
        ('email',     'Email'),
        ('whatsapp',  'WhatsApp'),
    ], string='Canal', default='email', readonly=True)
    email_from = fields.Char('Remitente / Teléfono', readonly=True)
    email_asunto = fields.Char('Asunto / Contacto', readonly=True, default='(sin asunto)')
    fecha_email = fields.Datetime('Fecha Recepción', readonly=True)

    estado = fields.Selection([
        ('pendiente',       'Pendiente'),
        ('procesado',       'Recibido OK'),
        ('con_diferencias', 'Con Diferencias'),
        ('sin_match',       'Sin Match'),
        ('rechazado',       'Rechazado — No es Retención'),
        ('duplicado',       'Posible Duplicado'),
        ('error',           'Error'),
        # 'Procesado' aquí NO significa "recibido/matcheado automáticamente" (eso
        # es la clave 'procesado' de arriba, relabeled a "Recibido OK") — significa
        # "revisado manualmente, no se hará más nada con esto" para los casos que
        # llegaron con diferencia/duplicado/rechazado/sin_match/error y ya se
        # gestionaron por otra vía. Se marca a mano con el botón "Marcar Procesado".
        ('cerrado',         'Procesado'),
    ], string='Estado', default='pendiente', required=True, tracking=True)

    wh_iva_id = fields.Many2one(
        've.wh.iva', string='Retención Vinculada', readonly=True)
    wh_iva_link_id = fields.Many2one(
        've.wh.iva', string='Vincular manualmente a',
        # Sin filtro de company_id a propósito — mismo motivo que el match
        # automático de _do_procesar: un despacho contable recibe por un
        # ÚNICO canal (email/WhatsApp) comprobantes de VARIAS compañías
        # cliente a la vez, y no se sabe a cuál pertenece el mensaje hasta
        # vincularlo a mano. Filtrar por company_id aquí (probado y
        # revertido 2026-07-22) le escondía a la usuaria la retención real
        # de OTRA compañía que necesitaba elegir. La corrección de
        # company_id ocurre DESPUÉS, en action_vincular_manual.
        domain="[('state', 'in', ['esperado', 'vencido']), ('estado_declaracion', '=', 'no_declarado')]")

    tipo_documento = fields.Selection([
        ('retencion_iva',   'Retención IVA'),
        ('comprobante_pago', 'Comprobante de Pago'),
        ('factura',          'Factura'),
        ('nota_credito',     'Nota de Crédito'),
        ('otro',             'Otro'),
    ], string='Tipo de Documento (OCR)', readonly=True)

    ocr_nro_control     = fields.Char('N° Control (OCR)',     readonly=True)
    ocr_rif             = fields.Char('RIF Agente (OCR)',     readonly=True)
    ocr_nombre_agente   = fields.Char('Agente (OCR)',         readonly=True)
    ocr_sujeto_rif      = fields.Char('RIF Sujeto Retenido (OCR)', readonly=True)
    ocr_sujeto_nombre   = fields.Char('Sujeto Retenido (OCR)', readonly=True)
    ocr_nro_comprobante = fields.Char('N° Comprobante (OCR)', readonly=True)
    ocr_periodo         = fields.Char('Período (OCR)',        readonly=True)
    ocr_monto_retenido  = fields.Float('Monto Retenido (OCR)', readonly=True, digits=(16, 2))
    monto_esperado      = fields.Float(
        'Monto Esperado', readonly=True, digits=(16, 2),
        help='monto_retenido calculado por SmartIVA para la retención vinculada, '
             'al momento de procesar el comprobante — para comparar contra el monto leído por OCR.')
    log                 = fields.Text('Log de Procesamiento', readonly=True)

    # ── Recepción de email ───────────────────────────────────────────────────

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        vals = dict(custom_values or {})
        vals.update({
            'email_from':   (msg_dict.get('email_from') or msg_dict.get('from', ''))[:200],
            'email_asunto': (msg_dict.get('subject') or '(sin asunto)')[:200],
            'fecha_email':  fields.Datetime.now(),
        })
        return super().message_new(msg_dict, vals)

    @staticmethod
    def _clean_email_body(body):
        """Elimina markup VML/Word de correos Outlook para mostrarlo limpio en el chatter."""
        # Bloques condicionales VML: <![if !vml]>...<![endif]>
        body = re.sub(r'<!\[if\s[^\]]*\]>.*?<!\[endif\]>', '', body,
                      flags=re.DOTALL | re.IGNORECASE)
        # Comentarios condicionales HTML: <!--[if ...]>...<![endif]-->
        body = re.sub(r'<!--\[if[^\]]*\]>.*?<!\[endif\]-->', '', body,
                      flags=re.DOTALL | re.IGNORECASE)
        # Tags VML residuales (v:imagedata, v:shape, o:p, etc.)
        body = re.sub(r'</?(?:v|o):[^>]*>', '', body, flags=re.IGNORECASE)
        return body

    def _message_post_after_hook(self, message, msg_vals):
        """Auto-procesa cuando llega email con adjuntos de imagen/PDF."""
        super()._message_post_after_hook(message, msg_vals)
        # Limpiar markup VML/Word del cuerpo del email en el chatter
        if message.message_type == 'email' and message.body:
            clean = self._clean_email_body(message.body)
            if clean != message.body:
                message.sudo().write({'body': clean})
        # Procesa si es un email entrante (no comentario manual) con imagen/PDF
        # y el registro no está ya procesado exitosamente
        if (message.message_type == 'email'
                and message.attachment_ids
                and self.estado != 'procesado'):
            if any(any(a.name.lower().endswith(e) for e in _IMG_EXT)
                   for a in message.attachment_ids):
                try:
                    self._do_procesar()
                except Exception as exc:
                    _logger.exception('ve.comprobante.inbox auto-process: %s', exc)
                    self._save_log([f'Error en procesamiento automático: {exc}'], 'error')

    # ── OCR (Claude Vision) ──────────────────────────────────────────────────

    def _ocr_claude(self, raw_bytes):
        """Extrae campos del comprobante usando Claude Vision. Retorna dict o None."""
        api_key = self.env['ir.config_parameter'].sudo().get_param(
            've_retencion_iva.anthropic_api_key', '').strip()
        if not api_key:
            return None

        b64 = base64.b64encode(raw_bytes).decode('utf-8')
        if raw_bytes[:4] == b'%PDF':
            cb = {'type': 'document',
                  'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': b64}}
        else:
            mt = 'image/png' if raw_bytes[:8] == b'\x89PNG\r\n\x1a\n' else 'image/jpeg'
            cb = {'type': 'image', 'source': {'type': 'base64', 'media_type': mt, 'data': b64}}

        prompt = (
            'Analiza este documento, que debería ser un comprobante de retención IVA '
            'venezolano pero podría ser otra cosa (factura, comprobante de pago, nota de '
            'crédito). Responde SOLO con un objeto JSON válido sin texto adicional ni ```.\n'
            'Los montos pueden aparecer en formato venezolano (130.000,00 = 130000.00); '
            'devuélvelos SIEMPRE como número decimal, ejemplo: 130000.00\n'
            '{\n'
            '  "tipo_documento": "uno de: retencion_iva | comprobante_pago | factura | '
            'nota_credito | otro",\n'
            '  "nro_comprobante": "número de 14 dígitos del comprobante o null",\n'
            '  "nro_control": "número de control formato 00-XXXXXXX o null",\n'
            '  "rif_agente": "RIF del agente de retención (quien retiene, el cliente '
            'que emite el comprobante) formato J-XXXXXXXX-X o null",\n'
            '  "nombre_agente": "razón social del agente de retención o null",\n'
            '  "sujeto_retenido_rif": "RIF del sujeto retenido (a quien le retienen el '
            'IVA — la empresa vendedora/proveedora, NO el agente) formato '
            'J-XXXXXXXX-X o null",\n'
            '  "sujeto_retenido_nombre": "razón social del sujeto retenido o null",\n'
            '  "periodo_fiscal": "período yyyy-mm (ej: 2026-05) o null",\n'
            '  "fecha_emision": "fecha DD/MM/YYYY o null",\n'
            '  "base_imponible_16": base imponible alícuota 16% como número ej 130000.00 o null,\n'
            '  "iva_causado_16": IVA causado al 16% como número ej 20800.00 o null,\n'
            '  "base_imponible_8": base imponible alícuota 8% como número o null,\n'
            '  "iva_causado_8": IVA causado al 8% como número o null,\n'
            '  "monto_retenido": monto total retenido como número ej 15600.00 o null\n'
            '}\n'
            'Si hay una sola alícuota (p.ej. 16%), completa base_imponible_16 e iva_causado_16 '
            'y deja base_imponible_8 e iva_causado_8 en null. '
            'Si hay dos alícuotas, completa ambos pares.'
        )
        payload = json.dumps({
            'model': 'claude-sonnet-4-6',
            'max_tokens': 512,
            'messages': [{'role': 'user', 'content': [
                cb, {'type': 'text', 'text': prompt},
            ]}],
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            text = result['content'][0]['text'].strip()
            text = re.sub(r'```(?:json)?', '', text).strip().strip('`').strip()
            return json.loads(text)
        except Exception as exc:
            _logger.warning('VeInbox OCR-Claude: %s', exc)
            return None

    # ── Procesamiento ────────────────────────────────────────────────────────

    def action_procesar(self):
        for rec in self:
            rec._do_procesar()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_cerrar(self):
        """Marca manualmente que no se hará más nada con este registro — para
        los casos que llegaron con diferencia/duplicado/rechazado/sin_match/
        error y ya se revisaron y gestionaron por otra vía (ej. se llamó al
        cliente, se resolvió por fuera del sistema, etc.)."""
        ahora = fields.Datetime.now()
        usuario = self.env.user.name
        for rec in self:
            rec.message_post(
                body=Markup(
                    '<b>Marcado como Procesado</b> manualmente por <b>{usuario}</b> '
                    'el {fecha} — no se realizar&#225; ninguna acci&#243;n adicional '
                    'sobre este registro.'
                ).format(usuario=escape(usuario), fecha=ahora.strftime('%d/%m/%Y %H:%M')),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
        self.write({'estado': 'cerrado'})
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _separar_adjuntos_extra(self, atts_extra, log):
        """Mueve cada adjunto además del primero a su propio registro nuevo
        de Buzón y lo procesa independientemente (MEJORA-CANAL-04). Se
        REASIGNA (no se copia) el `ir.attachment` al nuevo registro — si se
        copiara, `self` seguiría teniendo todos los adjuntos originales y un
        futuro "Re-intentar" volvería a separarlos de nuevo, duplicando
        registros cada vez."""
        for att in atts_extra:
            nuevo = self.copy({
                'estado':         'pendiente',
                'wh_iva_id':      False,
                'wh_iva_link_id': False,
            })
            att.sudo().write({'res_model': nuevo._name, 'res_id': nuevo.id})
            nuevo.message_post(
                body=Markup(
                    'Comprobante separado autom&#xe1;ticamente de {origen} '
                    '&#x2014; el mensaje original tra&#xed;a m&#xe1;s de un '
                    'adjunto, cada uno se procesa como un comprobante '
                    'independiente.'
                ).format(origen=self._get_html_link()),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
            log.append(f'  Adjunto "{att.name}" separado a registro id={nuevo.id}')
            nuevo._do_procesar()

    def _do_procesar(self):
        self.ensure_one()
        log = [f'Email: {self.email_from}', f'Asunto: {self.email_asunto}', '']

        atts_formato = self.env['ir.attachment'].search([
            ('res_model', '=', self._name), ('res_id', '=', self.id),
        ]).filtered(lambda a: any(a.name.lower().endswith(e) for e in _IMG_EXT))

        if not atts_formato:
            self._save_log(log + ['Sin adjuntos de imagen/PDF.'], 'sin_match')
            return

        # Validación de tamaño — descarta adjuntos improbablemente pequeños o
        # demasiado grandes para el OCR (MEJORA-CANAL-02).
        atts = atts_formato.filtered(lambda a: _TAM_MIN <= (a.file_size or 0) <= _TAM_MAX)
        rechazados = atts_formato - atts
        if rechazados:
            for a in rechazados:
                log.append(
                    f'  Rechazado por tamaño: {a.name} ({(a.file_size or 0) / 1024:.1f} KB)')
        if not atts:
            motivo = (
                f'el archivo pesa menos de {_TAM_MIN // 1024} KB o más de '
                f'{_TAM_MAX // (1024 * 1024)} MB.'
            )
            self._notificar_remitente(motivo)
            self._save_log(log + ['Todos los adjuntos rechazados por tamaño inválido.'], 'error')
            return

        log.append(f'Adjuntos imagen/PDF: {len(atts)}')

        # MEJORA-CANAL-04: varios adjuntos en un mismo mensaje son
        # comprobantes INDEPENDIENTES (pueden ser de compañías distintas en
        # Multiempresa — un único canal atiende a varias compañías cliente a
        # la vez) — antes el ciclo de OCR de abajo se detenía en el primer
        # adjunto legible y descartaba el resto en silencio. Cada adjunto
        # además del primero se separa a su propio registro de Buzón y se
        # procesa de forma independiente; este registro (`self`) sigue el
        # resto del método normalmente, mismo que si solo hubiera llegado uno.
        if len(atts) > 1:
            log.append(
                f'{len(atts)} adjuntos detectados — cada uno se procesa como '
                'un comprobante independiente (pueden ser de compañías distintas).')
            self._separar_adjuntos_extra(atts[1:], log)
            atts = atts[:1]

        ocr_data = None
        for att in atts:
            log.append(f'  Procesando: {att.name}')
            try:
                raw = base64.b64decode(att.datas)
                ocr_data = self._ocr_claude(raw)
                if ocr_data:
                    log.append('  OCR Claude Vision: OK ✓')
                    break
            except Exception as exc:
                log.append(f'  Error: {exc}')

        if not ocr_data:
            self._notificar_remitente(
                'no pudimos leer el contenido del archivo enviado.')
            self._save_log(
                log + ['OCR sin resultado. Configure ve_retencion_iva.anthropic_api_key.'],
                'error',
            )
            return

        def _f(key):
            return str(ocr_data.get(key) or '').strip()

        def _n(key):
            val = ocr_data.get(key)
            if val is None:
                return 0.0
            if isinstance(val, (int, float)):
                return float(val)
            s = str(val).strip()
            if ',' in s:
                # formato venezolano: "8.976,58" → 8976.58
                s = s.replace('.', '').replace(',', '.')
            else:
                s = s.replace(',', '')
            try:
                return float(s)
            except (ValueError, TypeError):
                return 0.0

        tipo_doc = _f('tipo_documento') or 'otro'
        if tipo_doc not in dict(self._fields['tipo_documento'].selection):
            tipo_doc = 'otro'
        self.write({'tipo_documento': tipo_doc})

        if tipo_doc != 'retencion_iva':
            log.append(
                f'Tipo de documento detectado: {tipo_doc} — no es retención IVA, '
                'no se intenta matching automático.')
            msg_inbox = self.message_post(
                body=Markup(
                    'Documento recibido no parece ser un comprobante de retenci&#243;n IVA '
                    '&#x2014; tipo detectado: <b>{tipo}</b>. Revisar manualmente.'
                ).format(tipo=escape(tipo_doc)),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
            if msg_inbox and atts:
                msg_inbox.sudo().write({'attachment_ids': [(4, a.id) for a in atts]})
            self._save_log(log, 'rechazado')
            return

        nro_control     = _f('nro_control')
        rif             = _f('rif_agente')
        nro_comp        = _f('nro_comprobante')
        nombre_agente   = _f('nombre_agente')
        sujeto_rif      = _f('sujeto_retenido_rif')
        sujeto_nombre   = _f('sujeto_retenido_nombre')
        periodo         = _f('periodo_fiscal')
        monto           = _n('monto_retenido')
        base_16         = _n('base_imponible_16')
        iva_16          = _n('iva_causado_16')
        base_8          = _n('base_imponible_8')
        iva_8           = _n('iva_causado_8')

        att_nombres = ', '.join(a.name for a in atts)
        log += [
            f'Canal recepción: {self.canal_origen}',
            f'Archivo(s):      {att_nombres}',
            f'N° Control:      {nro_control or "(no detectado)"}',
            f'RIF Agente:      {rif or "(no detectado)"}',
            f'Nombre Agente:   {nombre_agente or "(no detectado)"}',
            f'Sujeto Retenido: {sujeto_nombre or "(no detectado)"} '
            f'(RIF {sujeto_rif or "(no detectado)"})',
            f'N° Comprobante:  {nro_comp or "(no detectado)"}',
            f'Período:         {periodo or "(no detectado)"}',
            f'Base 16%:        {base_16:,.2f} Bs',
            f'IVA 16%:         {iva_16:,.2f} Bs',
            f'Base 8%:         {base_8:,.2f} Bs',
            f'IVA 8%:          {iva_8:,.2f} Bs',
            f'Monto Retenido:  {monto:,.2f} Bs',
            '',
        ]
        self.write({
            'ocr_nro_control':     nro_control,
            'ocr_rif':             rif,
            'ocr_nombre_agente':   nombre_agente,
            'ocr_sujeto_rif':      sujeto_rif,
            'ocr_sujeto_nombre':   sujeto_nombre,
            'ocr_nro_comprobante': nro_comp,
            'ocr_periodo':         periodo,
            'ocr_monto_retenido':  monto,
        })

        # ── Detección de compañía por Sujeto Retenido ───────────────────────
        # El comprobante trae el RIF/nombre de la propia empresa (la que está
        # SIENDO retenida), distinto del "agente de retención" (el cliente
        # que retiene). En un despacho contable con varias compañías cliente
        # recibiendo por un ÚNICO canal, esto es lo único que permite saber a
        # qué compañía pertenece el comprobante ANTES de intentar cualquier
        # match — no depender de que el match con ve.wh.iva tenga éxito (si
        # falla, o si hay ambigüedad de RIF, el registro se quedaba con la
        # compañía por defecto de la sesión que lo creó, típicamente DJCS).
        empresa_detectada = self.env['res.company']
        if sujeto_rif:
            rif_emp_c = re.sub(r'[-\s]', '', sujeto_rif).upper()
            empresa_detectada = self.env['res.company'].sudo().search(
                [('vat', '!=', False)]
            ).filtered(
                lambda c, r=rif_emp_c: re.sub(r'[-\s]', '', c.vat or '').upper() == r
            )[:1]
        if not empresa_detectada and sujeto_nombre:
            empresa_detectada = self.env['res.company'].sudo().search(
                [('name', 'ilike', sujeto_nombre)], limit=1)

        if empresa_detectada:
            log.append(
                f'Compañía detectada por Sujeto Retenido: {empresa_detectada.name}')
            if empresa_detectada.id != self.company_id.id:
                self.sudo().company_id = empresa_detectada.id
        else:
            log.append(
                'Sujeto Retenido: no se pudo detectar la compañía por RIF/nombre '
                '— se mantiene la compañía con la que se creó el registro.')

        # ── Match con retención existente ────────────────────────────────
        # Regla: si el OCR detectó tanto RIF como N° Control, deben coincidir
        # LOS DOS a la vez con la misma retención (no basta con uno solo) —
        # evita el caso real de un cliente con 2+ retenciones pendientes donde
        # el N° Control por sí solo (sin cruzar contra el RIF) podía traer la
        # equivocada, o donde un N° Control mal leído por OCR coincidiera por
        # casualidad con el de otro agente.
        # sudo(): un despacho contable puede recibir por un ÚNICO WhatsApp/email
        # los comprobantes de VARIAS compañías cliente a la vez — en ese caso
        # no sabemos todavía a qué compañía pertenece este mensaje, así que el
        # match tiene que poder ENCONTRAR la retención sin importar en qué
        # compañía esté (no se puede filtrar por company_id antes de saber
        # cuál es). Sin sudo(), el ir.rule de company_id podría filtrar en
        # silencio las compañías fuera de las "Compañías permitidas" del
        # usuario/proceso que ejecuta este método, y una retención real de
        # otra compañía nunca aparecería en el match (falso "sin_match").
        WhIva = self.env['ve.wh.iva'].sudo()
        wh = self.env['ve.wh.iva']

        # Sin filtro de compañía a propósito (mismo motivo documentado
        # arriba), PERO excluyendo compañías ARCHIVADAS — bug real
        # encontrado 2026-07-30: archivar una compañía (patrón estándar de
        # este proyecto para "eliminar" una de prueba, ver
        # feedback_odoo_bloqueos_por_contabilidad) no borra sus partners/
        # retenciones, y sin este filtro seguían contando como match real
        # para el chequeo de duplicado — un comprobante nuevo, legítimo,
        # para la compañía activa recién creada salía "Posible Duplicado"
        # contra datos de una compañía muerta.
        partner = self.env['res.partner']
        if rif:
            rif_c = re.sub(r'[-\s]', '', rif).upper()
            partner = self.env['res.partner'].sudo().search(
                ['|', ('company_id', '=', False), ('company_id.active', '!=', False),
                 ('vat', '!=', False)], limit=0,
            ).filtered(
                lambda p: re.sub(r'[-\s]', '', p.vat or '').upper() == rif_c
            )[:1]

        # Excluye compañías archivadas — mismo motivo que el filtro de
        # partner de arriba (ver comentario ahí).
        _ACTIVA = ['|', ('company_id', '=', False), ('company_id.active', '!=', False)]

        ambiguo_rif = False
        if nro_control and partner:
            # Ambos datos disponibles: deben coincidir juntos.
            wh = WhIva.search([
                ('nro_control', '=', nro_control),
                ('partner_id', '=', partner.id),
                ('state', 'in', ('esperado', 'vencido')),
                ('estado_declaracion', '=', 'no_declarado'),
            ] + _ACTIVA, limit=1)
            if wh:
                log.append(f'Match N° Control + RIF → {partner.name}')
            else:
                log.append(
                    f'N° Control "{nro_control}" no encontrado para el RIF {rif} '
                    '— no se adivina por uno solo de los dos.')
        elif nro_control and not rif:
            # Solo N° Control disponible (RIF no detectado por OCR) — único dato para buscar.
            wh = WhIva.search([
                ('nro_control', '=', nro_control),
                ('state', 'in', ('esperado', 'vencido')),
                ('estado_declaracion', '=', 'no_declarado'),
            ] + _ACTIVA, limit=1)
            if wh:
                log.append(f'Match solo por N° Control (sin RIF detectado) → {wh.partner_id.name}')
        elif not nro_control and partner:
            # Solo RIF disponible (N° Control no detectado por OCR) — único dato para buscar,
            # con resguardo de ambigüedad si el cliente tiene más de una retención pendiente.
            candidatos = WhIva.search([
                ('partner_id', '=', partner.id),
                ('state', 'in', ('esperado', 'vencido')),
                ('estado_declaracion', '=', 'no_declarado'),
            ] + _ACTIVA, order='id desc')
            if len(candidatos) == 1:
                wh = candidatos
                log.append(f'Match solo por RIF (sin N° Control detectado) → {partner.name}')
            elif len(candidatos) > 1:
                ambiguo_rif = True
                log.append(
                    f'RIF → {partner.name} tiene {len(candidatos)} retenciones '
                    f'pendientes ({", ".join(candidatos.mapped("nro_control"))}) — '
                    'ambiguo sin N° Control, no se puede elegir automáticamente.')

        if wh:
            # La retención encontrada es la que determina a qué compañía
            # pertenece este comprobante — no el valor por defecto con el que
            # se creó el registro del Buzón (self.env.company al momento de
            # recibir el mensaje), que en un canal compartido entre varias
            # compañías cliente del despacho probablemente esté equivocado.
            if wh.company_id and wh.company_id != self.company_id:
                self.sudo().company_id = wh.company_id.id
            monto_esperado = wh.monto_retenido
            self._aplicar_match(wh, nro_comp, atts,
                                ocr_base_16=base_16, ocr_iva_16=iva_16,
                                ocr_base_8=base_8, ocr_iva_8=iva_8,
                                ocr_monto=monto, canal=self.canal_origen)
            log.append(f'Retención id={wh.id} → Estado: Recibido ✓')
            log.append(f'Canal guardado: {self.canal_origen}')

            tiene_diferencia = (
                monto > 0 and monto_esperado > 0
                and abs(monto - monto_esperado) > 0.01
            )
            self.write({'monto_esperado': monto_esperado})
            if tiene_diferencia:
                dif = monto - monto_esperado
                log.append(
                    f'⚠ DIFERENCIA DE MONTO — esperado {monto_esperado:,.2f} Bs, '
                    f'comprobante {monto:,.2f} Bs, diferencia {dif:,.2f} Bs'
                )
                self._post_diferencia_monto(wh, monto, monto_esperado)
                self._save_log(log, 'con_diferencias', wh_iva_id=wh.id)
            else:
                self._save_log(log, 'procesado', wh_iva_id=wh.id)
        elif ambiguo_rif:
            msg_inbox = self.message_post(
                body=Markup(
                    '&#x26A0; <b>Varias retenciones pendientes para este RIF</b> — '
                    'no se pudo identificar cu&#225;l por N&#xba; Control (no detectado o '
                    'sin coincidencia exacta). Revisar y vincular manualmente.<br/>'
                    'Archivo: <b>{archivo}</b>'
                ).format(archivo=escape(att_nombres)),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
            if msg_inbox and atts:
                msg_inbox.sudo().write({'attachment_ids': [(4, a.id) for a in atts]})
            self._save_log(log, 'sin_match')
        else:
            # ── Detección de posible duplicado ────────────────────────────
            # Mismo N° Control/RIF, pero en un estado que ya NO es esperado/vencido
            # (ya recibido/confirmado/etc.) — la retención ya tiene su comprobante,
            # esto que llegó ahora es casi con seguridad un reenvío duplicado.
            # Mismo criterio que el match principal: si hay RIF y N° Control, deben
            # coincidir los dos a la vez (reusa el `partner` ya resuelto arriba).
            wh_dup = self.env['ve.wh.iva']
            if nro_control and partner:
                wh_dup = WhIva.search(
                    [('nro_control', '=', nro_control), ('partner_id', '=', partner.id)]
                    + _ACTIVA, limit=1)
            elif nro_control and not rif:
                wh_dup = WhIva.search([('nro_control', '=', nro_control)] + _ACTIVA, limit=1)
            elif not nro_control and partner:
                wh_dup = WhIva.search(
                    [('partner_id', '=', partner.id)] + _ACTIVA, limit=1, order='id desc')

            if wh_dup and wh_dup.declarado_sin_comprobante and not wh_dup.comp_monto_retenido:
                # No es un reenvío duplicado: es "Declarado sin Comprobante"
                # (nunca recibió el papel físico) y esto que llega ahora es
                # la PRIMERA llegada real del comprobante — mismo caso que ya
                # cubre el botón "Adjuntar Comprobante" a mano
                # (wizard_subir_comprobante.py), solo que por este canal
                # automático. El chequeo de duplicado de arriba solo miraba
                # si YA EXISTÍA un ve.wh.iva con ese RIF/N° Control, sin
                # distinguir si ese registro ya tenía comprobante real.
                log.append(
                    f'Retención id={wh_dup.id} está "Declarado sin Comprobante" '
                    '— esto es la primera llegada real del papel, no un duplicado.')
                self._procesar_llegada_tardia(
                    wh_dup, nro_comp, atts, monto,
                    base_16=base_16, iva_16=iva_16, base_8=base_8, iva_8=iva_8)
                self._save_log(log, 'procesado', wh_iva_id=wh_dup.id)
                return

            if wh_dup:
                factura_dup = wh_dup.invoice_id.name if wh_dup.invoice_id else (wh_dup.nro_documento or wh_dup.nro_control or '—')
                log.append(f'Duplicado — ya existe comprobante para esta factura ({factura_dup}).')
                msg_inbox = self.message_post(
                    body=Markup(
                        '&#x26A0; <b>Duplicado</b> — ya existe comprobante para esta factura '
                        '({link}).'
                    ).format(link=wh_dup._get_html_link()),
                    message_type='comment', subtype_xmlid='mail.mt_note',
                )
                if msg_inbox and atts:
                    msg_inbox.sudo().write({'attachment_ids': [(4, a.id) for a in atts]})
                self.write({'wh_iva_link_id': wh_dup.id})
                self._save_log(log, 'duplicado')
                return

            log.append('Sin match automático — use "Vincular manualmente".')
            # Deja el archivo recibido visible en la auditoría (chatter) aunque
            # no haya match — antes solo quedaba el ir.attachment "silencioso"
            # ligado al registro, sin ningún mensaje que lo referenciara, así
            # que no se podía ver a simple vista qué llegó y cuándo.
            msg_inbox = self.message_post(
                body=Markup(
                    'Sin match autom&#xe1;tico &#x2014; comprobante recibido '
                    'pero no se encontr&#xf3; una retenci&#xf3;n pendiente con '
                    'ese N&#xb0; Control/RIF.<br/>Archivo: <b>{archivo}</b>'
                ).format(archivo=escape(att_nombres)),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            if msg_inbox and atts:
                msg_inbox.sudo().write({'attachment_ids': [(4, a.id) for a in atts]})
            self._save_log(log, 'sin_match')

    def _aplicar_match(self, wh, nro_comp, atts,
                       ocr_base=0.0, ocr_iva=0.0, ocr_monto=0.0, canal=None,
                       ocr_base_16=0.0, ocr_iva_16=0.0, ocr_base_8=0.0, ocr_iva_8=0.0):
        # Guard defensivo: este método siempre pisa state='borrador' — el
        # match automático de _do_procesar ya filtra por
        # state in ('esperado','vencido') antes de llegar aquí, y el dominio
        # de wh_iva_link_id ("Vincular manualmente a") también, pero un
        # dominio de campo NO es una validación de servidor (un valor ya
        # seleccionado antes de que el registro pasara a Declarado, o
        # cualquier llamada futura, puede saltárselo). Bug real confirmado
        # en vivo 2026-07-22: una retención en 'declarado'
        # (declarado_sin_comprobante=True) quedó pisada a 'borrador' al
        # vincular manualmente un comprobante duplicado desde el Buzón.
        # Desde la Etapa 3 del rediseño de 3 ejes esto se simplifica en vez
        # de complicarse: 'confirmado' ya estaba bloqueado antes SIN
        # importar si además estaba conciliado/declarado (esos dejaron de
        # ser valores de `state`), así que basta con confirmado/anulado —
        # MÁS estado_declaracion=='declarado' explícito: una retención
        # "Declarado sin Comprobante" se queda en state esperado/vencido
        # para siempre (nunca pasó por Confirmado), así que sin este chequeo
        # extra el match automático/manual normal (_aplicar_match, que
        # SIEMPRE pisa state='borrador') la encontraría por su state y la
        # corrompería — el camino correcto para esa retención es
        # _procesar_llegada_tardia, no este método.
        _LOCKED = {'confirmado', 'anulado'}
        if wh.state in _LOCKED or wh.estado_declaracion == 'declarado':
            estado_label = dict(wh._fields['state'].selection).get(wh.state, wh.state)
            raise UserError(
                f'La retención {wh.ref or wh.id} ya está en estado "{estado_label}" '
                '— no se puede vincular un comprobante nuevo sin antes revertir ese '
                'estado (ej. "Deshacer Declaración" o el flujo correspondiente).'
            )
        att_nombres = ', '.join(a.name for a in atts)
        canal = canal or self.canal_origen or 'email'

        # Copiar adjunto también a la factura vinculada
        if wh.invoice_id:
            for att in atts:
                self.env['ir.attachment'].create({
                    'name':      att.name,
                    'datas':     att.datas,
                    'res_model': 'account.move',
                    'res_id':    wh.invoice_id.id,
                    'mimetype':  att.mimetype,
                })

        update = {
            'state':               'borrador',
            'canal_recepcion':     canal,
        }
        if not wh.fecha:
            update['fecha'] = fields.Date.today()
        if nro_comp and not wh.name:
            update['name'] = nro_comp

        # Asignar montos al campo correcto según alícuota ─────────────────────
        # Caso 1: OCR devolvió bases separadas por alícuota (nuevo formato)
        if ocr_base_16 > 0 or ocr_base_8 > 0:
            if ocr_base_16 > 0:
                update['comp_base_16'] = ocr_base_16
            if ocr_iva_16 > 0:
                update['comp_iva_16'] = ocr_iva_16
            if ocr_base_8 > 0:
                update['comp_base_8'] = ocr_base_8
            if ocr_iva_8 > 0:
                update['comp_iva_8'] = ocr_iva_8
        # Caso 2: fallback legacy — OCR devolvió una sola base; inferir alícuota
        # a partir de la retención esperada
        elif ocr_base > 0:
            solo_8 = wh.monto_base_red > 0 and wh.monto_base == 0
            if solo_8:
                update['comp_base_8'] = ocr_base
                if ocr_iva > 0:
                    update['comp_iva_8'] = ocr_iva
            else:
                update['comp_base_16'] = ocr_base
                if ocr_iva > 0:
                    update['comp_iva_16'] = ocr_iva

        if ocr_monto > 0:
            update['comp_monto_retenido'] = ocr_monto
        wh.write(update)

        factura_ref = (
            Markup('<br/>Factura: <b>{}</b>').format(escape(wh.invoice_id.name))
            if wh.invoice_id else Markup('')
        )
        # Crear copias de los adjuntos en ve.wh.iva (los originales quedan en el buzón)
        wh_atts = self.env['ir.attachment']
        for att in atts:
            wh_atts |= att.sudo().copy({
                'res_model': wh._name,
                'res_id':    wh.id,
            })

        # Chatter en la retención vinculada
        msg_wh = wh.message_post(
            body=Markup(
                '<b>Comprobante recibido por {canal}</b><br/>'
                'De: {de}<br/>'
                'Archivo: <b>{archivo}</b> | Canal: <b>{canal}</b>{factura}'
            ).format(
                canal=escape(canal),
                de=escape(self.email_from),
                archivo=escape(att_nombres),
                factura=factura_ref,
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        if msg_wh and wh_atts:
            msg_wh.sudo().write({'attachment_ids': [(4, a.id) for a in wh_atts]})

        # Chatter en el buzón (adjuntos originales siguen en el buzón)
        msg_inbox = self.message_post(
            body=Markup(
                'Procesado &#x2713; — Vinculado a <b>{wh}</b><br/>'
                'Archivo: <b>{archivo}</b>'
            ).format(
                wh=escape(wh.ref or str(wh.id)),
                archivo=escape(att_nombres),
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        if msg_inbox and atts:
            msg_inbox.sudo().write({'attachment_ids': [(4, a.id) for a in atts]})

    def _post_diferencia_monto(self, wh, monto_ocr, monto_esperado):
        """Registra en el chatter (buzón + retención vinculada) la diferencia
        entre el monto leído por OCR y el monto esperado calculado por SmartIVA."""
        dif = monto_ocr - monto_esperado
        cuerpo = Markup(
            '&#x26A0;&#xFE0F; <b>Diferencia de monto detectada</b><br/>'
            'Monto esperado (SmartIVA): <b>{esperado:,.2f} Bs</b><br/>'
            'Monto en el comprobante (OCR): <b>{ocr:,.2f} Bs</b><br/>'
            'Diferencia: <b>{dif:,.2f} Bs</b><br/>'
            'Revisar el comprobante y confirmar si corresponde a un error del '
            'agente de retención, un error de lectura OCR, o un ajuste válido.'
        ).format(esperado=monto_esperado, ocr=monto_ocr, dif=dif)
        self.message_post(body=cuerpo, message_type='comment', subtype_xmlid='mail.mt_note')
        wh.message_post(body=cuerpo, message_type='comment', subtype_xmlid='mail.mt_note')

    def _procesar_llegada_tardia(self, wh, nro_comp, atts, monto,
                                  base_16=0.0, iva_16=0.0, base_8=0.0, iva_8=0.0):
        """Llena los datos del comprobante físico de una retención
        'declarado_sin_comprobante=True' que nunca lo había recibido. No
        cambia `state` (ya está en 'declarado') — mismo criterio que
        wizard_subir_comprobante.py::action_guardar para este caso."""
        canal = self.canal_origen or 'email'
        update = {}
        if not wh.canal_recepcion:
            update['canal_recepcion'] = canal
        if nro_comp and not wh.name:
            update['name'] = nro_comp
        if base_16 > 0:
            update['comp_base_16'] = base_16
        if iva_16 > 0:
            update['comp_iva_16'] = iva_16
        if base_8 > 0:
            update['comp_base_8'] = base_8
        if iva_8 > 0:
            update['comp_iva_8'] = iva_8
        if monto > 0:
            update['comp_monto_retenido'] = monto
        if update:
            wh.write(update)

        for att in atts:
            self.env['ir.attachment'].create({
                'name':      att.name,
                'datas':     att.datas,
                'res_model': 've.wh.iva',
                'res_id':    wh.id,
            })

        wh.message_post(
            body=Markup(
                '<b>Comprobante recibido tarde</b> — esta retención se declaró '
                '&#x201c;Declarado sin Comprobante&#x201d; y ahora lleg&#xf3; el papel '
                'f&#xed;sico por el Buz&#xf3;n ({canal}).<br/>'
                'Monto retenido seg&#xfa;n comprobante: <b>{monto:,.2f} Bs</b>'
            ).format(canal=canal, monto=monto),
            message_type='comment', subtype_xmlid='mail.mt_note',
        )

        monto_esperado = wh.monto_retenido
        if monto > 0 and monto_esperado > 0 and abs(monto - monto_esperado) > 0.01:
            self._post_diferencia_monto(wh, monto, monto_esperado)

    def action_vincular_manual(self):
        self.ensure_one()
        if not self.wh_iva_link_id:
            raise UserError(
                'Seleccione una retención en el campo "Vincular manualmente a".')
        wh = self.wh_iva_link_id
        # Ver comentario en _do_procesar sobre por qué la retención
        # encontrada determina la compañía real del comprobante, no el
        # valor con el que se creó el registro del Buzón.
        if wh.company_id and wh.company_id != self.company_id:
            self.sudo().company_id = wh.company_id.id
        monto_esperado = wh.monto_retenido
        atts = self.env['ir.attachment'].search([
            ('res_model', '=', self._name), ('res_id', '=', self.id),
        ])
        self._aplicar_match(
            wh, self.ocr_nro_comprobante, atts,
            ocr_base=0.0, ocr_iva=0.0, ocr_monto=self.ocr_monto_retenido,
        )

        tiene_diferencia = (
            self.ocr_monto_retenido > 0 and monto_esperado > 0
            and abs(self.ocr_monto_retenido - monto_esperado) > 0.01
        )
        if tiene_diferencia:
            self._post_diferencia_monto(wh, self.ocr_monto_retenido, monto_esperado)

        self.write({
            'wh_iva_id':       wh.id,
            'estado':          'con_diferencias' if tiene_diferencia else 'procesado',
            'wh_iva_link_id':  False,
            'monto_esperado':  monto_esperado,
            'log':             (self.log or '') + '\nVinculado manualmente.',
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _notificar_remitente(self, motivo):
        """Avisa al remitente cuando su envío no se pudo procesar. Por email,
        responde directo a la dirección de origen. Por WhatsApp no hay canal
        de respuesta implementado todavía — queda solo en la bitácora interna."""
        self.ensure_one()
        cuerpo = Markup(
            '<p>No pudimos procesar su env&#237;o de comprobante de retenci&#243;n IVA.</p>'
            '<p><b>Motivo:</b> {motivo}</p>'
            '<p>Por favor reenv&#237;e el comprobante verificando el archivo.</p>'
        ).format(motivo=escape(motivo))
        if self.canal_origen == 'email' and self.email_from and '@' in self.email_from:
            self.env['mail.mail'].sudo().create({
                'subject': 'No se pudo procesar su comprobante de retención IVA',
                'body_html': cuerpo,
                'email_to': self.email_from,
                'auto_delete': True,
            }).send()
            self.message_post(
                body=Markup('Notificaci&#243;n de error enviada por email a {}.').format(
                    escape(self.email_from)),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )
        else:
            self.message_post(
                body=Markup(
                    '&#x26A0; No se pudo notificar al remitente autom&#225;ticamente '
                    '(canal {canal} sin respuesta implementada) — motivo: {motivo}'
                ).format(canal=escape(self.canal_origen or ''), motivo=escape(motivo)),
                message_type='comment', subtype_xmlid='mail.mt_note',
            )

    def _save_log(self, log_lines, estado, wh_iva_id=None):
        vals = {'estado': estado, 'log': '\n'.join(log_lines)}
        if wh_iva_id:
            vals['wh_iva_id'] = wh_iva_id
        self.write(vals)
