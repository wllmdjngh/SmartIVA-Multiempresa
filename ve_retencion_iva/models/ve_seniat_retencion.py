import logging
from datetime import date as _date

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class VeSeniatRetencion(models.Model):
    _name = 've.seniat.retencion'
    _description = 'Retenciones descargadas del SENIAT'
    _order = 'fecha desc'
    _rec_name = 'nro_control'

    name = fields.Char(string='N° Comprobante')
    rif_agente = fields.Char(string='RIF Agente de Retención', required=True)
    nombre_agente = fields.Char(string='Nombre Agente')
    periodo = fields.Char(string='Período Fiscal', required=True)

    # Campo plano (no computed): se calcula en create() y onchange,
    # pero puede ser sobreescrito directamente por la API o el wizard.
    periodo_retencion = fields.Char(
        string='Período Declaración',
        help='Formato: yyyy-mm 1Q ó yyyy-mm 2Q. Ej: 2026-04 1Q',
    )

    fecha = fields.Date(string='Fecha Operación')
    monto_base = fields.Float(string='Base Imponible', digits=(16, 2))
    monto_retenido = fields.Float(string='Monto Retenido', digits=(16, 2))
    tipo_documento = fields.Char(string='Tipo Documento')
    monto_documento = fields.Float(string='Monto del Documento', digits=(16, 2))
    monto_exento = fields.Float(string='Monto Exento', digits=(16, 2))
    nro_documento = fields.Char(string='Nro. Documento')
    nro_control = fields.Char(string='Nro. Control', required=True)
    doc_afectado = fields.Char(string='Número Documento Afectado')
    alicuota = fields.Float(string='Alícuota', digits=(5, 2), aggregator=False)

    estado = fields.Selection([
        ('cargado',    'Por Conciliar'),
        ('conciliado', 'Conciliado'),
        ('diferencia', 'Con Diferencia'),
        ('sin_match',  'Sin Coincidencia'),
    ], string='Estado Conciliación', default='cargado')

    wh_iva_id = fields.Many2one('ve.wh.iva', string='Retención Odoo Vinculada', ondelete='set null')
    nivel_match = fields.Selection([
        ('n1', 'N1 — RIF + N° Control'),
        ('n2', 'N2 — RIF + N° Factura'),
    ], string='Nivel de Match', copy=False,
        help='Mismo campo/criterio que ve.wh.iva.nivel_match, ver ese modelo.')
    conciliacion_id = fields.Many2one(
        've.conciliacion.periodo', string='Período de Conciliación', ondelete='set null')
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        related='conciliacion_id.company_id', store=True)
    cargado_por_rpa = fields.Boolean(string='Cargado por RPA', default=True)
    fecha_carga = fields.Datetime(string='Fecha de Carga', default=fields.Datetime.now)
    is_locked = fields.Boolean(string='Bloqueado', compute='_compute_is_locked')

    # Pedido explícito de la usuaria 2026-08-05, para auditar rápido el
    # caso confirmado por RPC ese mismo día: un mismo N° Control puede
    # repetirse legítimamente (ej. "00" como relleno genérico de SENIAT)
    # para VARIAS retenciones de la MISMA compañía + RIF Agente, siempre
    # que traigan N° Documento distinto -- no es un bug, pero conviene
    # poder filtrarlas y revisarlas juntas.
    #
    # Dos intentos previos con un campo Boolean(search=...) -- el primero
    # con SQL crudo (se saltaba las reglas multi-compañía), el segundo
    # con self.search() normal pero con un bug real de Odoo no resuelto:
    # =True y =False devolvían el MISMO domain (confirmado comparando
    # search_read con ambos valores) -- el mecanismo de negación de un
    # campo Boolean con search= y sin store/compute no funcionaba en esta
    # versión. Se reemplaza por un campo REAL guardado en la base,
    # mantenido al día en create()/write() -- así el filtro usa la
    # evaluación normal de dominio de Odoo, sin depender de ese mecanismo.
    nro_control_repetido = fields.Boolean(
        string='N° Control con varios N° Documento', default=False, copy=False,
        help='Mismo N° Control + RIF Agente + Compañía aparece en más de '
             'un registro con N° Documento distinto -- caso real (SENIAT '
             'a veces usa un N° Control genérico tipo "00" para varias '
             'retenciones), no necesariamente un error de carga.',
    )

    def _recompute_nro_control_repetido_grupo(self):
        """Recalcula nro_control_repetido para TODOS los registros que
        comparten clave (nro_control+rif_agente+company_id) con `self` --
        no solo self: agregar o cambiar UNA fila puede cambiar el flag de
        las OTRAS del mismo grupo (ej. la 2da fila de un N° Control antes
        único ahora lo vuelve "repetido" para ambas)."""
        claves = {
            (r.nro_control, r.rif_agente, r.company_id.id)
            for r in self if r.nro_control
        }
        if not claves:
            return
        domain = ['|'] * (len(claves) - 1)
        for ctrl, rif, comp in claves:
            domain += ['&', '&',
                       ('nro_control', '=', ctrl), ('rif_agente', '=', rif),
                       ('company_id', '=', comp)]
        grupo = self.sudo().search(domain)
        docs_por_clave = {}
        for r in grupo:
            k = (r.nro_control, r.rif_agente, r.company_id.id)
            docs_por_clave.setdefault(k, set()).add(r.nro_documento)
        a_true = grupo.filtered(
            lambda r: len(docs_por_clave[(r.nro_control, r.rif_agente, r.company_id.id)]) > 1
            and not r.nro_control_repetido)
        a_false = grupo.filtered(
            lambda r: len(docs_por_clave[(r.nro_control, r.rif_agente, r.company_id.id)]) <= 1
            and r.nro_control_repetido)
        ctx = {'skip_recompute_nro_control_repetido': True}
        if a_true:
            a_true.with_context(**ctx).write({'nro_control_repetido': True})
        if a_false:
            a_false.with_context(**ctx).write({'nro_control_repetido': False})

    # ── Lógica de período quincenal ────────────────────────────────────────────

    @staticmethod
    def _calc_pr(periodo, fecha):
        """Calcula el período quincenal (yyyy-mm 1Q/2Q) desde la FECHA REAL —
        día <= 15 → 1Q del propio mes de la fecha; día >= 16 → 2Q del propio
        mes de la fecha, SIN desplazar al mes siguiente. Misma regla que
        `_calc_periodo_retencion` (controllers/api__rpa.py) y
        `ve.conciliacion.periodo._asegurar_periodo()`.

        Bug real encontrado 2026-08-04 (Cementos, carga XLSX): esta función
        tenía la lógica VIEJA (quincena invertida + salto de mes) que ya se
        había corregido en las otras 2 copias el 2026-08-01 pero nunca se
        sincronizó acá — una carga XLSX abierta desde el período "2025-12
        1Q" metía TODAS las filas de diciembre en 1Q sin importar su fecha
        real, porque el resultado de esta función casi nunca coincidía con
        ninguna de las 2 ventanas que comparaba contra `periodo`, cayendo
        siempre al default. `periodo` solo se usa como respaldo cuando no
        hay fecha utilizable — el resultado normal se deriva 100% de la
        fecha, igual que las otras 2 copias."""
        if fecha:
            if isinstance(fecha, str):
                try:
                    fecha = _date.fromisoformat(str(fecha)[:10])
                except (ValueError, TypeError):
                    fecha = None
            if fecha:
                quincena = '1Q' if fecha.day <= 15 else '2Q'
                return f'{fecha.year:04d}-{fecha.month:02d} {quincena}'
        return f'{periodo} 2Q' if periodo else False

    @api.onchange('periodo', 'fecha')
    def _onchange_periodo_fecha(self):
        for rec in self:
            rec.periodo_retencion = self._calc_pr(rec.periodo, rec.fecha)

    # ── ORM overrides ──────────────────────────────────────────────────────────

    @staticmethod
    def _periodo_from_fecha(fecha):
        """Deriva (periodo, periodo_retencion) desde una fecha de operación —
        misma regla que _calc_pr (día <= 15 → 1Q, día >= 16 → 2Q, propio
        mes de la fecha, sin desplazar). Tenía la misma lógica vieja
        invertida/desplazada que _calc_pr, corregida el mismo día."""
        if not fecha:
            return None, None
        if isinstance(fecha, str):
            try:
                fecha = _date.fromisoformat(str(fecha)[:10])
            except (ValueError, TypeError):
                return None, None
        periodo = f'{fecha.year:04d}-{fecha.month:02d}'
        quincena = '1Q' if fecha.day <= 15 else '2Q'
        return periodo, f'{periodo} {quincena}'

    @api.model_create_multi
    def create(self, vals_list):
        _logger.info('ve_seniat_retencion.create called: n=%d', len(vals_list))
        ConcModel = self.env['ve.conciliacion.periodo']
        for vals in vals_list:
            # Si viene conciliacion_id pero falta periodo (mes fiscal), derivarlo.
            # OJO: periodo_retencion (la QUINCENA) NUNCA se hereda a ciegas del
            # período padre acá -- bug real encontrado 2026-08-04 (Cementos,
            # carga XLSX): el wizard (wizard_carga_seniat.py) manda
            # conciliacion_id fijo (el período desde el que se abrió el
            # wizard) sin periodo_retencion propio por fila, y este bloque
            # ANTES backfilleaba periodo_retencion directo del padre --
            # TODAS las filas de diciembre quedaban en "2025-12 1Q" (el
            # período del wizard) sin importar que varias fueran realmente
            # de 2Q según su propia fecha. periodo_retencion se deja SIN
            # tocar acá a propósito -- lo calcula la lógica de abajo
            # (_calc_pr) desde la fecha real de cada fila, igual que ya
            # hace el controller del RPA.
            if vals.get('conciliacion_id') and not vals.get('periodo'):
                try:
                    conc = ConcModel.browse(int(vals['conciliacion_id']))
                    if conc.exists():
                        vals['periodo'] = conc.periodo or ''
                except (ValueError, TypeError):
                    pass
            # Si periodo sigue sin estar, derivarlo desde fecha (RPA vía JSON-RPC)
            _logger.info(
                've_seniat_retencion.create: nro_control=%r  periodo_in=%r  '
                'fecha=%r  pr_in=%r',
                vals.get('nro_control'), vals.get('periodo'),
                vals.get('fecha'), vals.get('periodo_retencion'),
            )
            if not vals.get('periodo') and vals.get('fecha'):
                p, pr = self._periodo_from_fecha(vals['fecha'])
                _logger.info(
                    've_seniat_retencion.create: _periodo_from_fecha → p=%r  pr=%r', p, pr)
                if p:
                    vals['periodo'] = p
                    if not vals.get('periodo_retencion'):
                        vals['periodo_retencion'] = pr
            # Calcular periodo_retencion desde periodo+fecha si aún no está
            if not vals.get('periodo_retencion') and vals.get('periodo'):
                vals['periodo_retencion'] = self._calc_pr(
                    vals['periodo'], vals.get('fecha'))
        records = super().create(vals_list)
        # Vincular a conciliación por periodo_retencion cuando no viene explícita
        for rec in records:
            if rec.conciliacion_id or not rec.periodo_retencion:
                continue
            conc = ConcModel.search(
                [('periodo_retencion', '=', rec.periodo_retencion)], limit=1)
            if conc:
                rec.conciliacion_id = conc.id
        # Al final -- company_id (related de conciliacion_id) ya debe estar
        # resuelto para poder agrupar bien.
        records._recompute_nro_control_repetido_grupo()
        return records

    def write(self, vals):
        res = super().write(vals)
        campos_clave = {'nro_control', 'nro_documento', 'rif_agente', 'conciliacion_id'}
        if not self.env.context.get('skip_recompute_nro_control_repetido') and campos_clave & set(vals.keys()):
            self._recompute_nro_control_repetido_grupo()
        return res

    @api.depends('estado', 'conciliacion_id.estado')
    def _compute_is_locked(self):
        # Mismo patrón que ve.wh.iva._compute_is_locked: bloqueada si ya se
        # conciliό (estado propio) O si el período padre ya fue declarado
        # ante el SENIAT (antes no dependía de esto último -- una retención
        # recién cargada en un período ya declarado quedaba editable).
        for rec in self:
            rec.is_locked = (
                rec.estado != 'cargado'
                or rec.conciliacion_id.estado == 'declarado'
            )
