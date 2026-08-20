import base64
import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from markupsafe import Markup
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VeDeclaracionIva(models.Model):
    _name = 've.declaracion.iva'
    _description = 'Declaración IVA Venezuela (Forma 30)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'periodo_retencion desc'
    _rec_name = 'name'

    name = fields.Char(string='Referencia', copy=False, readonly=True)

    # ── Vínculo 1:1 con el período de conciliación ────────────────────────────
    conciliacion_id = fields.Many2one(
        've.conciliacion.periodo',
        string='Período Conciliación',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='conciliacion_id.company_id',
        string='Empresa',
        store=True,
        index=True,
    )
    periodo_retencion = fields.Char(
        string='Período',
        related='conciliacion_id.periodo_retencion',
        store=True,
    )
    fecha_inicio = fields.Date(related='conciliacion_id.fecha_inicio', store=True)
    fecha_fin = fields.Date(related='conciliacion_id.fecha_fin', store=True)

    # ── Retenciones Clientes (IVA cobrado — agentes SPE que nos retienen) ──────
    wh_iva_clientes_ids = fields.One2many(
        related='conciliacion_id.wh_iva_ids',
        string='Retenciones IVA Clientes',
        readonly=False,
    )

    # ── Retenciones Proveedores ───────────────────────────────────────────────
    wh_iva_prov_ids = fields.One2many(
        've.wh.iva.prov', 'declaracion_iva_id',
        string='Retenciones IVA Proveedores',
    )
    count_wh_iva_prov = fields.Integer(
        string='Comprobantes Prov.',
        compute='_compute_prov_summary', store=True,
    )
    total_retenido_prov = fields.Float(
        string='Total Retenido Prov.',
        compute='_compute_prov_summary', store=True, digits=(16, 2),
    )
    estado_prov = fields.Selection([
        ('pendiente', 'Pendiente'),
        ('declarado', 'Declarado'),
    ], string='Estado IVA Proveedores', default='pendiente', tracking=True,
       help='Se marca Declarado al enviar retenciones a proveedores al SENIAT.')
    declarado_prov_por_id = fields.Many2one(
        'res.users', string='Declarado Prov. por', readonly=True)
    fecha_declaracion_prov = fields.Datetime(
        string='Fecha Declaración Proveedores', readonly=True)
    nro_declaracion_prov = fields.Char(
        string='N° Planilla de Pago', tracking=True,
        help='Número de planilla de pago (10 dígitos) asignado por el SENIAT.')
    planilla_declaracion_prov = fields.Binary(
        string='Planilla Declaración Proveedores', attachment=True)
    planilla_declaracion_prov_nombre = fields.Char(string='Nombre Planilla Prov.')

    # ── Campos manuales / arrastre ────────────────────────────────────────────
    # Renombrado 2026-07-24 de "campo_33_arrastre" a "campo_54_arrastre": el
    # nombre viejo era un residuo histórico sin relación real con C.33 (que es
    # Base de Compras, ver campo_33_base más abajo) — el campo oficial SENIAT
    # correspondiente es C.54 "Retenciones Acumuladas por Descontar" (así lo
    # trata ya el reporte PDF, ver ve_reporte_seniat.xml fila 33). Es un campo
    # con datos reales guardados — el rename de columna va en una migración
    # (ver migrations/19.0.2.11.6/pre_migrate.py).
    campo_54_arrastre = fields.Float(
        string='C.54 — Retenciones Acumuladas por Descontar',
        digits=(16, 2), default=0.0,
        help='Saldo de retenciones IVA soportadas no descontadas del período '
             'anterior (campo oficial SENIAT C.54). Se copia automáticamente '
             'del período anterior al crear.',
    )
    campo_20 = fields.Float(
        string='C.20 — Excedente Créditos Mes Anterior',
        digits=(16, 2), default=0.0,
        help='Excedente de crédito fiscal (C.60) del período anterior. '
             'Se copia automáticamente del período anterior al crear.',
    )

    # ── Prorrateo créditos fiscales (Art. 34 LIVA) ────────────────────────────
    aplica_prorrata = fields.Boolean(
        string='Aplica Prorrateo (Art. 34 LIVA)', default=False,
        help='Marque si hay ventas mixtas (gravadas + exentas/no gravadas). '
             'El porcentaje de prorrata limita los créditos fiscales deducibles.')
    porcentaje_prorrata = fields.Float(
        string='% Prorrata',
        digits=(5, 2), default=100.0,
        help='Ventas gravadas / Total ventas × 100. Se aplica a C.71 (Art. 34 LIVA). '
             'Ejemplo: 80% → solo el 80% de los créditos de compras es deducible.')

    # ── Estado de vencimiento ─────────────────────────────────────────────────
    vencida = fields.Boolean(
        string='Vencida sin Declarar',
        compute='_compute_vencida', store=False,
        help='True si el plazo SENIAT venció (fecha_fin + 7 días < hoy) y aún no está presentada.')

    # ── Forma 30 — Débitos Fiscales (Ventas) ─────────────────────────────────
    campo_40_base = fields.Float(
        string='C.40 — Base Exenta (0%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_443_base = fields.Float(
        string='C.443 — Base Reducida (8%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_453_debito = fields.Float(
        string='C.453 — Débito Reducida (8%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_42_base = fields.Float(
        string='C.42 — Base General (16%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_42_debito = fields.Float(
        string='C.43 — Débito General (16%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_442_base = fields.Float(
        string='C.442 — Base Adicional (15%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_452_debito = fields.Float(
        string='C.452 — Débito Adicional (15%)',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_49 = fields.Float(
        string='C.49 — Total Débitos Fiscales',
        compute='_compute_reporte_seniat', digits=(16, 2))

    # ── Forma 30 — Créditos Fiscales (Compras) ───────────────────────────────
    campo_33_base = fields.Float(
        string='C.33 — Base Compras',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_33_credito = fields.Float(
        string='C.34 — Crédito Compras',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_71 = fields.Float(
        string='C.71 — Total Créditos Deducibles',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_39 = fields.Float(
        string='C.39 — Total Créditos Fiscales',
        compute='_compute_reporte_seniat', digits=(16, 2))

    # ── Forma 30 — Autoliquidación (cuota tributaria antes de retenciones) ───
    campo_53 = fields.Float(
        string='C.53 — Total Cuota Tributaria del Período',
        compute='_compute_reporte_seniat', digits=(16, 2),
        help='max(0, C.49 − C.39). Cero cuando el crédito fiscal supera al débito fiscal.')
    campo_60 = fields.Float(
        string='C.60 — Excedente Crédito Fiscal (mes siguiente)',
        compute='_compute_reporte_seniat', digits=(16, 2),
        help='max(0, C.39 − C.49). Se auto-copia como C.20 del período siguiente.')
    campo_78 = fields.Float(
        string='C.78 — Sub-total Impuesto a Pagar',
        compute='_compute_reporte_seniat', digits=(16, 2),
        help='C.53 − C.60, antes de aplicar las retenciones soportadas (ítem 74).')

    # ── Forma 30 — Retenciones Recibidas (Clientes SPE) ──────────────────────
    campo_66 = fields.Float(
        string='C.66 — Retenciones del Período',
        compute='_compute_reporte_seniat', digits=(16, 2))
    campo_74 = fields.Float(
        string='C.74 — Total Retenciones Disponibles',
        compute='_compute_reporte_seniat', digits=(16, 2),
        help='C.54 arrastre + C.66 período actual. Renombrado 2026-07-24 desde '
             '"campo_89" — C.89 no existe en la Forma 030 real; el reporte PDF '
             'ya mostraba este mismo valor bajo el ítem 74 oficial ("Total '
             'Retenciones").')
    campo_90 = fields.Float(
        string='C.90 — IVA a Pagar',
        compute='_compute_reporte_seniat', digits=(16, 2))
    saldo_nuevo_campo_54 = fields.Float(
        string='Nuevo Saldo C.54',
        compute='_compute_reporte_seniat', digits=(16, 2),
        help='Si las retenciones superan el impuesto a pagar (C.78), este saldo se '
             'arrastra al período siguiente como C.54.')

    # ── Estado y datos de presentación Forma 30 ──────────────────────────────
    estado = fields.Selection([
        ('borrador',    'Borrador'),
        ('presentada',  'Decl SENIAT'),
        ('sustitutiva', 'Sustitutiva'),
    ], string='Estado Declaración', default='borrador', tracking=True)
    declaracion_original_id = fields.Many2one(
        've.declaracion.iva',
        string='Declaración Original',
        help='Solo aplica si esta es una declaración sustitutiva.',
    )
    nro_declaracion = fields.Char(
        string='N° Certificado SENIAT', tracking=True)
    fecha_declaracion = fields.Datetime(string='Fecha Declaración')
    planilla_declaracion = fields.Binary(
        string='Planilla Declaración SENIAT', attachment=True)
    planilla_declaracion_nombre = fields.Char(string='Nombre Planilla')
    declarado_por_id = fields.Many2one('res.users', string='Declarado por')
    declarado_por_rpa = fields.Boolean(string='Declarado por RPA', default=False)

    # ── Estado conciliación SENIAT (relay desde el período) ─────────────────
    estado_conciliacion_periodo = fields.Selection(
        related='conciliacion_id.estado',
        string='Concilia IVA Cli',
        store=True,
    )

    # ── Alertas (relay desde la conciliación) ────────────────────────────────
    alerta_vencidos = fields.Char(
        related='conciliacion_id.alerta_vencidos', string='Vencidos')
    alerta_esperados = fields.Char(
        related='conciliacion_id.alerta_esperados', string='Esperados')
    alerta_borradores = fields.Char(
        related='conciliacion_id.alerta_borradores', string='Sin Confirmar')

    # ─────────────────────────────────────────────────────────────────────────
    # ORM
    # ─────────────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                conc_id = vals.get('conciliacion_id')
                if conc_id:
                    conc = self.env['ve.conciliacion.periodo'].browse(conc_id)
                    vals['name'] = f'DECL-IVA/{conc.periodo_retencion or conc_id}'
                else:
                    vals['name'] = 'DECL-IVA/nuevo'
        records = super().create(vals_list)
        for rec in records:
            self._auto_copiar_arrastre(rec)
        return records

    def _auto_copiar_arrastre(self, rec):
        anterior = self.search([
            ('estado', '=', 'presentada'),
            ('periodo_retencion', '<', rec.periodo_retencion or ''),
            ('id', '!=', rec.id),
        ], order='periodo_retencion desc', limit=1)
        if not anterior:
            return
        if not rec.campo_54_arrastre and anterior.saldo_nuevo_campo_54 > 0:
            rec.campo_54_arrastre = anterior.saldo_nuevo_campo_54
        if not rec.campo_20 and anterior.campo_60 > 0:
            rec.campo_20 = anterior.campo_60

    @api.model
    def _get_or_create_for_periodo(self, conciliacion_id):
        """Busca o crea la ve.declaracion.iva para el período dado. Idempotente."""
        decl = self.search([('conciliacion_id', '=', conciliacion_id)], limit=1)
        if not decl:
            decl = self.create({'conciliacion_id': conciliacion_id})
            conc = self.env['ve.conciliacion.periodo'].browse(conciliacion_id)
            conc.declaracion_iva_id = decl.id
        return decl

    # ─────────────────────────────────────────────────────────────────────────
    # Compute
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('estado', 'fecha_fin')
    def _compute_vencida(self):
        today = fields.Date.today()
        for rec in self:
            rec.vencida = (
                rec.estado != 'presentada'
                and bool(rec.fecha_fin)
                and (rec.fecha_fin + timedelta(days=7)) < today
            )

    @api.depends('wh_iva_prov_ids.state', 'wh_iva_prov_ids.monto_retenido')
    def _compute_prov_summary(self):
        for rec in self:
            activos = rec.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
            rec.count_wh_iva_prov = len(activos)
            rec.total_retenido_prov = sum(activos.mapped('monto_retenido'))

    @api.depends(
        'conciliacion_id.wh_iva_ids.monto_c66',
        'conciliacion_id.wh_iva_ids.state',
        'conciliacion_id.wh_iva_ids.incluir_declaracion',
        # Mismos campos, vía el path que realmente edita la vista (wh_iva_clientes_ids,
        # related='conciliacion_id.wh_iva_ids') — sin este alias, el toggle de C.66 en
        # el formulario no dispara el onchange que refresca este compute (MEJORA-UX-03).
        'wh_iva_clientes_ids.monto_c66',
        'wh_iva_clientes_ids.state',
        'wh_iva_clientes_ids.incluir_declaracion',
        'conciliacion_id.fecha_inicio',
        'conciliacion_id.fecha_fin',
        'campo_54_arrastre',
        'campo_20',
        'aplica_prorrata',
        'porcentaje_prorrata',
    )
    def _compute_reporte_seniat(self):
        for rec in self:
            conc = rec.conciliacion_id
            if not conc:
                rec.campo_40_base = rec.campo_42_base = rec.campo_42_debito = 0.0
                rec.campo_443_base = rec.campo_453_debito = 0.0
                rec.campo_442_base = rec.campo_452_debito = 0.0
                rec.campo_49 = rec.campo_33_base = rec.campo_33_credito = 0.0
                rec.campo_71 = rec.campo_39 = rec.campo_66 = 0.0
                rec.campo_53 = rec.campo_60 = rec.campo_78 = 0.0
                rec.campo_74 = rec.campo_90 = rec.saldo_nuevo_campo_54 = 0.0
                continue

            # MEJORA-UX-03 (reabierto 2026-07-20, causa real encontrada
            # 2026-07-24): usar rec.wh_iva_clientes_ids, NO conc.wh_iva_ids.
            # Son el mismo dato guardado (wh_iva_clientes_ids es
            # related='conciliacion_id.wh_iva_ids', readonly=False) pero en
            # un formulario sin guardar, el cliente web solo refleja los
            # cambios en memoria a través del campo que la vista edita de
            # verdad. Recorrer conciliacion_id.wh_iva_ids en el código vuelve
            # a traer el valor ya guardado (viejo) de incluir_declaracion —
            # el @api.depends sí disparaba el recompute, pero con datos
            # obsoletos, por eso el total nunca cambiaba al togglear C.66.
            activos = rec.wh_iva_clientes_ids.filtered(lambda r: r.state != 'anulado')
            company_id = conc.company_id.id or self.env.company.id

            # ── Débitos Fiscales desde facturas de venta del período ──────────
            base_40 = base_42 = debito_42 = base_443 = debito_443 = 0.0
            if conc.fecha_inicio and conc.fecha_fin:
                facturas = self.env['account.move'].search([
                    ('move_type', 'in', ('out_invoice', 'out_refund')),
                    ('state', '=', 'posted'),
                    ('invoice_date', '>=', conc.fecha_inicio),
                    ('invoice_date', '<=', conc.fecha_fin),
                    ('company_id', '=', company_id),
                ])
                for move in facturas:
                    sign = -1.0 if move.move_type == 'out_refund' else 1.0
                    for line in move.invoice_line_ids:
                        if line.display_type in ('line_section', 'line_note'):
                            continue
                        for tax in line.tax_ids:
                            if not hasattr(tax, 'amount'):
                                continue
                            if tax.amount == 0:
                                base_40 += sign * line.price_subtotal
                            elif abs(tax.amount - 16.0) < 0.01:
                                base_42   += sign * line.price_subtotal
                                debito_42 += sign * line.price_subtotal * 0.16
                            elif abs(tax.amount - 8.0) < 0.01:
                                base_443   += sign * line.price_subtotal
                                debito_443 += sign * line.price_subtotal * 0.08

            rec.campo_40_base    = base_40
            rec.campo_42_base    = base_42
            rec.campo_42_debito  = debito_42
            rec.campo_443_base   = base_443
            rec.campo_453_debito = debito_443
            rec.campo_442_base   = 0.0
            rec.campo_452_debito = 0.0
            rec.campo_49 = debito_42 + debito_443

            # ── Créditos Fiscales desde facturas de compra del período ────────
            base_33 = credito_33 = 0.0
            if conc.fecha_inicio and conc.fecha_fin:
                compras = self.env['account.move'].search([
                    ('move_type', 'in', ('in_invoice', 'in_refund')),
                    ('state', '=', 'posted'),
                    ('invoice_date', '>=', conc.fecha_inicio),
                    ('invoice_date', '<=', conc.fecha_fin),
                    ('company_id', '=', company_id),
                ])
                for move in compras:
                    sign = -1.0 if move.move_type == 'in_refund' else 1.0
                    for line in move.invoice_line_ids:
                        if line.display_type in ('line_section', 'line_note'):
                            continue
                        for tax in line.tax_ids:
                            if not hasattr(tax, 'amount'):
                                continue
                            if abs(tax.amount - 16.0) < 0.01:
                                base_33    += sign * line.price_subtotal
                                credito_33 += sign * line.price_subtotal * 0.16
                            elif abs(tax.amount - 8.0) < 0.01:
                                base_33    += sign * line.price_subtotal
                                credito_33 += sign * line.price_subtotal * 0.08

            rec.campo_33_base    = base_33
            rec.campo_33_credito = credito_33
            if rec.aplica_prorrata and rec.porcentaje_prorrata >= 0:
                rec.campo_71 = credito_33 * rec.porcentaje_prorrata / 100.0
            else:
                rec.campo_71 = credito_33
            rec.campo_39 = rec.campo_71 + rec.campo_20

            # ── Autoliquidación — cuota tributaria antes de retenciones ──────
            # C.53/C.60 son mutuamente excluyentes (uno de los dos es siempre
            # 0): si el débito supera al crédito, C.53 es la cuota a pagar y
            # C.60 queda en 0; si el crédito supera al débito, el excedente
            # va a C.60 (arrastra como C.20 del período siguiente) y C.53
            # queda en 0 — nunca se resta contra las retenciones del período.
            rec.campo_53 = max(0.0, rec.campo_49 - rec.campo_39)
            rec.campo_60 = max(0.0, rec.campo_39 - rec.campo_49)
            rec.campo_78 = rec.campo_53 - rec.campo_60

            # ── C.66 — Retenciones recibidas de clientes SPE ─────────────────
            rec.campo_66 = sum(activos.mapped('monto_c66'))
            rec.campo_74 = rec.campo_54_arrastre + rec.campo_66
            rec.campo_90 = rec.campo_78 - rec.campo_74
            rec.saldo_nuevo_campo_54 = max(0.0, -rec.campo_90)

    # ─────────────────────────────────────────────────────────────────────────
    # Acciones — IVA Proveedores
    # ─────────────────────────────────────────────────────────────────────────

    def action_abrir_wizard_declarar_prov(self):
        self.ensure_one()
        if self.estado_prov == 'declarado':
            raise UserError('Los comprobantes de IVA Proveedores ya fueron declarados al SENIAT.')
        activos = self.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
        if not activos:
            raise UserError(
                'No hay comprobantes activos de retención a proveedores en este período.\n'
                'Agregue al menos un comprobante antes de declarar.'
            )
        en_borrador = activos.filtered(lambda r: r.state == 'borrador')
        if en_borrador:
            refs = ', '.join(r.ref or '(sin ref)' for r in en_borrador)
            raise UserError(
                f'Hay {len(en_borrador)} comprobante(s) en Borrador pendientes de confirmar.\n\n'
                f'Comprobantes: {refs}'
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Declarar IVA Proveedores al SENIAT',
            'res_model': 've.wizard.declarar.prov',
            'view_mode': 'form',
            'context': {'default_declaracion_iva_id': self.id},
            'target': 'new',
        }

    def action_marcar_prov_declarado(self, nro, fecha, user, planilla=False, nombre=False):
        """Llamado desde wizard_declarar_prov tras validar datos."""
        self.ensure_one()
        activos = self.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
        # Mismo control que action_abrir_wizard_declarar_prov, repetido aquí
        # a propósito (defensa en profundidad): este método es el que de
        # verdad declara, no debe confiar en que quien lo llamó ya validó
        # esto — evita declarar con comprobantes que ni siquiera se enviaron
        # al proveedor todavía.
        en_borrador = activos.filtered(lambda r: r.state == 'borrador')
        if en_borrador:
            refs = ', '.join(r.ref or '(sin ref)' for r in en_borrador)
            raise UserError(
                f'No se puede declarar: hay {len(en_borrador)} comprobante(s) '
                f'en Borrador pendientes de confirmar.\n\nComprobantes: {refs}'
            )
        activos.write({
            'state': 'declarado',
            'declarado_por_id': user.id,
            'fecha_declaracion': fecha,
        })
        vals = {
            'estado_prov': 'declarado',
            'nro_declaracion_prov': nro,
            'declarado_prov_por_id': user.id,
            'fecha_declaracion_prov': fecha,
        }
        if planilla:
            vals['planilla_declaracion_prov'] = planilla
            vals['planilla_declaracion_prov_nombre'] = nombre or 'declaracion_iva_prov.pdf'
        self.write(vals)
        self.message_post(
            body=Markup(
                '<b>IVA Proveedores declarado</b> — N° <b>{n}</b><br/>'
                'Declarado por <b>{u}</b>. {c} comprobante(s) marcados como Declarados.'
            ).format(n=nro, u=user.name, c=len(activos)),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Acciones — Forma 30
    # ─────────────────────────────────────────────────────────────────────────

    def action_abrir_wizard_declarar(self):
        self.ensure_one()
        if self.conciliacion_id.estado != 'aprobado':
            raise UserError(
                'La conciliación debe estar en estado "Conciliación Aprobada" antes de declarar.'
            )
        prov_activos = self.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
        if prov_activos and self.estado_prov != 'declarado':
            raise UserError(
                'No se puede declarar la Forma 030 mientras el IVA Proveedores esté pendiente.\n\n'
                f'Hay {len(prov_activos)} comprobante(s) de retención a proveedores sin declarar.\n'
                'Use "Declarar IVA Proveedores" primero.'
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Confirmar Declaración SENIAT',
            'res_model': 've.wizard.declarar',
            'view_mode': 'form',
            'context': {'default_declaracion_iva_id': self.id},
            'target': 'new',
        }

    def _marcar_wh_iva_declarados(self):
        """Marca como declaradas (Eje 3: estado_declaracion) las retenciones
        Clientes del período con C.66 activo. Compartido por
        action_marcar_declarado y action_registrar_declaracion_rpa (antes
        duplicaban ~15 líneas idénticas). Ya NO toca `state` — desde la
        Etapa 3 del rediseño de 3 ejes, declarar es independiente de la
        recepción del comprobante físico.

        Devuelve (para_declarar, sin_comprobante) para que el caller arme
        su propio mensaje de chatter.
        """
        _SENIAT_OK = frozenset({
            'conciliada_norec', 'listo_declarar', 'declarado',
            'conciliada', 'aprobado_declarar',
        })
        # 'confirmado' incluido a propósito: son retenciones CON comprobante
        # físico que quedaron ahí por no calzar 1:1 con el SENIAT
        # (diferencia de monto o sin match) — su monto_c66 SÍ se suma al
        # Campo 66 (ver ve_wh_iva.py::_compute_monto_c66, no filtra por
        # estado_conciliacion), así que también deben poder declararse.
        para_declarar = self.conciliacion_id.wh_iva_ids.filtered(
            lambda r: r.state == 'confirmado' and r.incluir_declaracion
        )
        if para_declarar:
            # Solo se sobreescribe estado_conciliacion a 'declarado' cuando ya
            # era un match limpio con el SENIAT — si tenía diferencia o no
            # había match, ese dato se conserva (no se debe "blanquear" un
            # descuadre real solo porque se presentó la declaración).
            matched = para_declarar.filtered(lambda r: r.estado_conciliacion in _SENIAT_OK)
            sin_match_exacto = para_declarar - matched
            if matched:
                matched.write({'estado_declaracion': 'declarado', 'estado_conciliacion': 'declarado'})
            if sin_match_exacto:
                sin_match_exacto.write({'estado_declaracion': 'declarado'})
        # Retenciones marcadas en C.66 sin haber pasado nunca por "Confirmado"
        # (No Recibido/Vencido, incluidas a mano porque el SENIAT ya la
        # reporta) — quedan declaradas SIN mover `state` (se queda reflejando
        # la realidad: el papel nunca llegó), marcadas con
        # declarado_sin_comprobante=True para el seguimiento, y SIN tocar
        # estado_conciliacion (conciliada_norec/diferencia/solo_odoo se
        # conserva — es la distinción real de si el SENIAT la tiene o no,
        # independiente de si llegó el papel físico).
        sin_comprobante = self.conciliacion_id.wh_iva_ids.filtered(
            lambda r: r.state in ('esperado', 'vencido') and r.incluir_declaracion
        )
        if sin_comprobante:
            sin_comprobante.write({'estado_declaracion': 'declarado', 'declarado_sin_comprobante': True})
        para_declarar += sin_comprobante
        return para_declarar, sin_comprobante

    def action_marcar_declarado(self):
        """Llamado desde ve.wizard.declarar tras capturar N° y planilla."""
        self.ensure_one()
        if not self.nro_declaracion:
            raise UserError('N° Certificado SENIAT no especificado.')
        now = fields.Datetime.now()
        self.write({
            'estado': 'presentada',
            'declarado_por_id': self.env.user.id,
            'declarado_por_rpa': False,
            'fecha_declaracion': now,
        })
        self.conciliacion_id.write({'estado': 'declarado'})
        para_declarar, sin_comprobante = self._marcar_wh_iva_declarados()
        sin_planilla = not self.planilla_declaracion
        self.message_post(
            body=Markup(
                '<b>Declaración IVA presentada al SENIAT</b> — N° <b>{n}</b><br/>'
                'Presentada por <b>{u}</b>. {k} comprobante(s) marcados como Declarados'
                '{k2}.{aviso}'
            ).format(
                n=self.nro_declaracion,
                u=self.env.user.name,
                k=len(para_declarar),
                k2=(f' ({len(sin_comprobante)} sin comprobante físico — requieren seguimiento)'
                    if sin_comprobante else ''),
                aviso=Markup(
                    '<br/>⚠ <em>Recuerde subir la Planilla de Declaración.</em>'
                ) if sin_planilla else '',
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def action_deshacer_declaracion(self):
        self.ensure_one()
        if self.estado != 'presentada':
            raise UserError('Solo se puede deshacer una declaración en estado "Presentada".')
        declarados = self.conciliacion_id.wh_iva_ids.filtered(lambda r: r.estado_declaracion == 'declarado')
        # Desde la Etapa 3 del rediseño de 3 ejes, declarar ya no toca
        # `state` — no hay nada que adivinar para revertir (antes había que
        # recalcular si un "sin comprobante" volvía a esperado o vencido
        # comparando su fecha límite contra hoy). Deshacer es solo apagar
        # el Eje 3 + restaurar estado_conciliacion SOLO en los que
        # realmente se habían "blanqueado" a 'declarado' al presentar
        # (mismo criterio inverso que _marcar_wh_iva_declarados) — un
        # descuadre real (diferencia/sin match) que nunca se tocó al
        # declarar tampoco debe tocarse al deshacer.
        reblanqueados = declarados.filtered(lambda r: r.estado_conciliacion == 'declarado')
        if reblanqueados:
            reblanqueados.write({'estado_conciliacion': 'listo_declarar'})
        sin_comprobante = declarados.filtered(lambda r: r.declarado_sin_comprobante)
        if sin_comprobante:
            sin_comprobante.write({'declarado_sin_comprobante': False})
        con_comprobante = declarados - sin_comprobante
        if declarados:
            declarados.write({'estado_declaracion': 'no_declarado'})
        self.write({
            'estado': 'borrador',
            'declarado_por_id': False,
            'declarado_por_rpa': False,
            'fecha_declaracion': False,
            'nro_declaracion': False,
        })
        self.conciliacion_id.write({'estado': 'aprobado'})
        self.message_post(
            body=Markup(
                '<b>Declaración revertida</b> por <b>{u}</b>.<br/>'
                '{k} comprobante(s) vueltos a No Declarado.{k2}'
            ).format(
                u=self.env.user.name, k=len(con_comprobante),
                k2=(f'<br/>{len(sin_comprobante)} comprobante(s) sin comprobante físico '
                    f'vueltos a seguimiento (No Recibido/Vencido).' if sin_comprobante else ''),
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def action_declarar_rpa(self):
        self.ensure_one()
        rpa_url = self.env['ir.config_parameter'].sudo().get_param(
            've_retencion_iva.rpa_declaracion_url', ''
        )
        if not rpa_url:
            raise UserError(
                'El RPA de declaración no está configurado.\n'
                'Configure el parámetro: ve_retencion_iva.rpa_declaracion_url'
            )
        conc = self.conciliacion_id
        if not conc.company_id.vat:
            raise UserError(
                f'La compañía "{conc.company_id.name}" no tiene RIF configurado '
                '(Ajustes → Compañías) — el RPA lo necesita para identificar '
                'con qué credenciales entrar al SENIAT.'
            )
        rif_cliente_aet = conc.company_id.ve_rif_cliente_aet()
        payload = json.dumps({
            # Dos niveles — AET tiene una BD por CLIENTE (ver
            # res.company.ve_rif_cliente, típicamente el RIF del despacho
            # contable) y credenciales SENIAT por EMPRESA dentro de esa BD.
            # Nunca viajan credenciales en este payload ni se guardan en
            # Odoo. Revisado 2026-07-27: ya NO usa res.company.parent_id
            # (Sucursales) — ver nota en ve_conciliacion.py::action_extraer_seniat.
            'rif_cliente': rif_cliente_aet,
            'rif_empresa': conc.company_id.vat,
            'declaracion_iva_id': self.id,
            'periodo_id': conc.id,
            'periodo': conc.periodo,
            'periodo_retencion': conc.periodo_retencion,
        }).encode('utf-8')
        req = urllib.request.Request(
            rpa_url, data=payload,
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode('utf-8', errors='replace')
            raise UserError(f'Error del RPA (HTTP {e.code}): {body}')
        except Exception as e:
            raise UserError(f'No se pudo contactar el RPA: {e}')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Declaración enviada al RPA',
                'message': f'El RPA procesará la declaración del período {conc.periodo}.',
                'type': 'info',
                'sticky': False,
            },
        }

    # ── Helpers TXT SENIAT ────────────────────────────────────────────────────

    @staticmethod
    def _rif_seniat(vat):
        """Devuelve RIF en formato SENIAT: letra + 9 dígitos, sin guiones, 10 chars."""
        if not vat:
            return 'J000000000'
        clean = ''.join(c for c in vat.upper() if c.isalnum())
        if not clean:
            return 'J000000000'
        letter = clean[0] if clean[0].isalpha() else 'J'
        digits = ''.join(c for c in clean if c.isdigit())
        return letter + digits.zfill(9)[:9]

    @staticmethod
    def _comp14(name, fecha):
        """Convierte N° comprobante (AAAAMM+seq) a 14 chars AAAAMMDDDDDDDD."""
        if name and len(name) >= 6:
            return name[:6] + name[6:].zfill(8)
        ym = fecha.strftime('%Y%m') if fecha else fields.Date.today().strftime('%Y%m')
        return ym + '00000000'

    def action_generar_txt(self):
        """Genera archivo TXT declaración retenciones IVA Proveedores (formato SENIAT)."""
        self.ensure_one()
        activos = self.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
        if not activos:
            raise UserError('No hay comprobantes activos de IVA Proveedores para generar el archivo.')

        en_borrador = activos.filtered(lambda r: r.state == 'borrador')
        if en_borrador:
            refs = ', '.join(r.name or r.ref or '(sin ref)' for r in en_borrador)
            raise UserError(
                f'No se puede generar TXT, faltan comprobantes por enviar.\n\nComprobantes en Borrador: {refs}'
            )

        rif_agente = self._rif_seniat(self.env.company.vat)
        rows = []

        for p in activos.sorted(key=lambda r: (r.fecha or fields.Date.today(), r.name or '')):
            fecha = p.fecha or fields.Date.today()
            periodo = fecha.strftime('%Y%m')
            fecha_str = fecha.strftime('%Y-%m-%d')
            tipo_doc = '03' if (p.invoice_id and p.invoice_id.move_type == 'in_refund') else '01'
            rif_prov = self._rif_seniat(p.partner_id.vat if p.partner_id else '')
            nro_factura = (p.invoice_id.ref or p.invoice_id.name or '0') if p.invoice_id else '0'
            # Col H: '0' cuando no hay N° Control
            nro_control_raw = (p.nro_control or '').strip()
            nro_control = nro_control_raw if nro_control_raw else '0'
            # Sin N° Control → retención obligatoria 100% (PA SNAT/2025/000054)
            pct = 100.0 if not nro_control_raw else p.porcentaje_retencion
            monto_total = (
                p.invoice_id.amount_total if p.invoice_id
                else p.monto_base_16 + p.monto_iva_16 + p.monto_base_8 + p.monto_iva_8
            )
            nro_comp = self._comp14(p.name, fecha)

            nro_afectada = '0'
            if tipo_doc == '03' and p.invoice_id:
                orig = getattr(p.invoice_id, 'reversed_entry_id', None)
                if orig:
                    nro_afectada = orig.ref or orig.name or '0'

            def _row(base, iva, alicuota, _pct=pct):
                retenido = round(iva * _pct / 100, 2)
                return '\t'.join([
                    rif_agente, periodo, fecha_str, 'C', tipo_doc,
                    rif_prov,
                    f'{nro_factura:<20}',   # G: alineado izq. 20 chars
                    f'{nro_control:<10}',   # H: alineado izq. 10 chars (00-0000000)
                    f'{monto_total:>15.2f}', f'{base:>15.2f}', f'{retenido:>15.2f}',
                    nro_afectada, nro_comp, '0.00', f'{alicuota:.2f}', '0',
                ])

            if p.monto_base_16 > 0:
                rows.append(_row(p.monto_base_16, p.monto_iva_16, 16.0))
            if p.monto_base_8 > 0:
                rows.append(_row(p.monto_base_8, p.monto_iva_8, 8.0))

        if not rows:
            raise UserError('No hay líneas con base imponible para generar el archivo.')

        content = '\r\n'.join(rows)
        periodo_nombre = (self.periodo_retencion or str(self.id)).replace(' ', '_')
        filename = f'ret_iva_prov_{periodo_nombre}.txt'

        att = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(content.encode('utf-8')),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/plain',
        })
        self.message_post(
            body=Markup(
                '<b>Archivo TXT IVA Proveedores generado</b><br/>'
                'Período: <b>{p}</b> · {n} línea(s) exportada(s)<br/>'
                'Generado por <b>{u}</b>.'
            ).format(
                p=self.periodo_retencion or self.name or '—',
                n=len(rows),
                u=self.env.user.name,
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
            attachment_ids=[att.id],
        )
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{att.id}?download=true',
            'target': 'self',
        }

    def action_reporte_seniat(self):
        self.ensure_one()
        return self.env.ref('ve_retencion_iva.action_report_reporte_seniat').report_action(self)

    def action_declarar_rpa_prov(self):
        self.ensure_one()
        raise UserError(
            'Declaración por RPA de IVA Proveedores pendiente de implementar.\n'
            'Use "Declarar IVA Proveedores" para registrar manualmente.'
        )

    def action_registrar_declaracion_rpa(self, nro_declaracion, fecha_declaracion):
        """Registra el resultado de la declaración recibido por callback del RPA."""
        self.ensure_one()
        self.write({
            'estado': 'presentada',
            'declarado_por_rpa': True,
            'nro_declaracion': nro_declaracion,
            'fecha_declaracion': fecha_declaracion,
        })
        self.conciliacion_id.write({'estado': 'declarado'})
        para_declarar, sin_comprobante = self._marcar_wh_iva_declarados()
        self.message_post(
            body=Markup(
                '<b>Declaración registrada por RPA</b> — N° <b>{n}</b><br/>'
                '{k} comprobante(s) marcados como Declarados.'
            ).format(n=nro_declaracion, k=len(para_declarar)),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
