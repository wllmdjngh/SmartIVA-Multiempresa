import base64
import logging

from markupsafe import Markup
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class VeWhIvaProv(models.Model):
    _name = 've.wh.iva.prov'
    _description = 'Comprobante Retención IVA Proveedores'
    _rec_name = 'ref'
    _order = 'fecha desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # ── Identificación ────────────────────────────────────────────────────
    name = fields.Char(
        string='N° Comprobante',
        copy=False,
        readonly=True,
        index=True,
        help='Número de comprobante (aaaaMM + 4 consecutivo = 10 dígitos), generado automáticamente.',
    )
    ref = fields.Char(
        string='Ref. Interna',
        copy=False,
        readonly=True,
        help='Referencia interna Odoo (RET-IVA-P/YYYY/NNNN).',
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Proveedor', tracking=True, index=True,
    )
    rif = fields.Char(string='RIF', related='partner_id.vat', store=True)

    # ── Factura de compra ─────────────────────────────────────────────────
    invoice_id = fields.Many2one(
        'account.move',
        string='Factura Proveedor',
        domain="[('move_type', 'in', ('in_invoice', 'in_refund')), "
               " ('state', '=', 'posted'), ('company_id', '=', company_id)]",
        tracking=True,
        index=True,
    )
    invoice_name = fields.Char(
        string='N° Factura',
        related='invoice_id.name',
        help='Solo el número de factura, sin la Referencia entre paréntesis '
             'que trae el widget de relación (Many2one) por defecto.',
    )
    nro_control = fields.Char(
        string='N° Control',
        tracking=True,
        help='N° Control de la factura del proveedor. '
             'Ausencia activa retención al 100%.',
    )

    # ── Fechas y período ──────────────────────────────────────────────────
    fecha = fields.Date(string='Fecha', default=fields.Date.today, tracking=True, index=True)
    declaracion_iva_id = fields.Many2one(
        've.declaracion.iva', string='Declaración IVA', index=True, tracking=True,
        help='Declaración IVA (Forma 30) a la que pertenece este comprobante.',
    )
    # Alias de compatibilidad: periodo_id sigue en la tabla pero ya no es el campo principal.
    # Se mantiene como Many2one readonly para no romper datos históricos durante la transición.
    periodo_id = fields.Many2one(
        've.conciliacion.periodo', string='Período (legado)',
        index=True, readonly=True, store=True,
        help='Campo legado. Usar declaracion_iva_id.',
    )
    periodo_retencion = fields.Char(
        string='Período', related='declaracion_iva_id.periodo_retencion', store=True,
    )

    # ── Montos ────────────────────────────────────────────────────────────
    monto_base_16 = fields.Float(string='Base 16%', digits=(16, 2), tracking=True)
    monto_iva_16 = fields.Float(
        string='IVA 16%', compute='_compute_montos_iva', store=True, digits=(16, 2),
    )
    monto_base_8 = fields.Float(string='Base 8%', digits=(16, 2), tracking=True)
    monto_iva_8 = fields.Float(
        string='IVA 8%', compute='_compute_montos_iva', store=True, digits=(16, 2),
    )
    monto_iva_total = fields.Float(
        string='IVA Total', compute='_compute_montos_iva', store=True, digits=(16, 2),
    )

    # ── Retención ─────────────────────────────────────────────────────────
    porcentaje_retencion = fields.Float(
        string='% Retención',
        digits=(5, 2),
        default=75.0,
        help='75% si el proveedor tiene N° Control (Contribuyente Especial); '
             '100% si no lo tiene (PA SNAT/2025/000054).',
        # Ver mismo fix en ve_wh_iva.py — sin esto, Odoo suma el % en el
        # pie de cualquier lista que lo muestre como columna.
        aggregator=False,
    )
    monto_retenido = fields.Float(
        string='Monto Retenido',
        compute='_compute_monto_retenido',
        store=True,
        digits=(16, 2),
        tracking=True,
    )

    # ── Pago ──────────────────────────────────────────────────────────────
    payment_id = fields.Many2one(
        'account.payment',
        string='Pago',
        ondelete='set null',
        tracking=True,
        help='Pago al proveedor en el que se aplicó esta retención.',
    )
    monto_neto_pago = fields.Float(
        string='Monto Neto a Pagar',
        compute='_compute_monto_neto_pago',
        store=True,
        digits=(16, 2),
        help='Total factura menos monto retenido — lo que efectivamente se paga al proveedor.',
    )

    # ── Estado ────────────────────────────────────────────────────────────
    state = fields.Selection(
        [
            ('borrador',  'Borrador'),
            ('enviado',   'Enviado'),
            ('declarado', 'Declarado'),
            ('anulado',   'Anulado'),
        ],
        string='Estado',
        default='borrador',
        required=True,
        tracking=True,
        index=True,
    )
    fecha_envio = fields.Datetime(string='Fecha Envío', readonly=True)
    enviado_por_id = fields.Many2one('res.users', string='Enviado por', readonly=True)
    declarado_por_id = fields.Many2one('res.users', string='Declarado por', readonly=True)
    fecha_declaracion = fields.Datetime(string='Fecha Declaración', readonly=True)

    # ─── Computes ─────────────────────────────────────────────────────────

    @api.depends('monto_base_16', 'monto_base_8')
    def _compute_montos_iva(self):
        for rec in self:
            rec.monto_iva_16 = round(rec.monto_base_16 * 0.16, 2)
            rec.monto_iva_8 = round(rec.monto_base_8 * 0.08, 2)
            rec.monto_iva_total = rec.monto_iva_16 + rec.monto_iva_8

    @api.depends('monto_iva_total', 'porcentaje_retencion')
    def _compute_monto_retenido(self):
        for rec in self:
            rec.monto_retenido = round(
                rec.monto_iva_total * rec.porcentaje_retencion / 100.0, 2
            )

    @api.depends('invoice_id', 'invoice_id.amount_total', 'monto_retenido')
    def _compute_monto_neto_pago(self):
        for rec in self:
            total = rec.invoice_id.amount_total if rec.invoice_id else 0.0
            rec.monto_neto_pago = round(total - rec.monto_retenido, 2)

    # ─── Onchanges ────────────────────────────────────────────────────────

    @api.onchange('invoice_id')
    def _onchange_invoice_id(self):
        if not self.invoice_id:
            return
        inv = self.invoice_id
        self.partner_id = inv.partner_id
        nro = getattr(inv, 'nro_control', '') or ''
        self.nro_control = nro
        self.porcentaje_retencion = 75.0 if nro.strip() else 100.0

        base_16 = base_8 = 0.0
        sign = -1.0 if inv.move_type == 'in_refund' else 1.0
        for line in inv.invoice_line_ids:
            if line.display_type in ('line_section', 'line_note'):
                continue
            for tax in line.tax_ids:
                if not hasattr(tax, 'amount'):
                    continue
                if abs(tax.amount - 16.0) < 0.01:
                    base_16 += sign * line.price_subtotal
                elif abs(tax.amount - 8.0) < 0.01:
                    base_8 += sign * line.price_subtotal
        self.monto_base_16 = round(base_16, 2)
        self.monto_base_8 = round(base_8, 2)

    @api.onchange('nro_control')
    def _onchange_nro_control(self):
        self.porcentaje_retencion = (
            75.0 if self.nro_control and self.nro_control.strip() else 100.0
        )

    # ─── CRUD ──────────────────────────────────────────────────────────────

    def _gen_nro_comprobante(self, fecha, company_id=None):
        import datetime
        d = fecha if fecha else fields.Date.today()
        if isinstance(d, str):
            d = datetime.date.fromisoformat(d)
        prefix = d.strftime('%Y%m')
        # Único por EMPRESA COMPRADORA (pedido explícito 2026-07-28) — sin
        # filtrar por company_id, el consecutivo se compartía entre todas
        # las compañías de la base, así que la compañía B podía saltarse
        # números o colisionar según lo que ya hubiera generado la A.
        domain = [('name', '=like', prefix + '%')]
        if company_id:
            domain.append(('company_id', '=', company_id))
        last = self.search(domain, order='name desc', limit=1)
        if last and last.name and last.name.startswith(prefix):
            try:
                serial = int(last.name[6:]) + 1
            except (ValueError, IndexError):
                serial = 1
        else:
            serial = 1
        return f'{prefix}{serial:04d}'

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env['ir.sequence'].next_by_code
        for vals in vals_list:
            if not vals.get('ref'):
                vals['ref'] = seq('ve.wh.iva.prov') or ''
            if not vals.get('name'):
                vals['name'] = self._gen_nro_comprobante(
                    vals.get('fecha'), vals.get('company_id'))
        return super().create(vals_list)

    # ─── Acciones ─────────────────────────────────────────────────────────

    def action_enviar(self):
        self.ensure_one()
        if self.state != 'borrador':
            raise UserError('Solo se puede enviar un comprobante en estado Borrador.')

        # Generar PDF del comprobante
        _REPORT_XMLID = 've_retencion_iva.action_report_comprobante_prov_retencion'
        pdf_bytes, _mime = self.env['ir.actions.report']._render_qweb_pdf(
            _REPORT_XMLID, [self.id]
        )
        filename = f'Comprobante_Ret_{self.name or self.ref}.pdf'
        att = self.env['ir.attachment'].create({
            'name':      filename,
            'type':      'binary',
            'datas':     base64.b64encode(pdf_bytes),
            'res_model': self._name,
            'res_id':    self.id,
            'mimetype':  'application/pdf',
        })

        now = fields.Datetime.now()
        self.write({
            'state':          'enviado',
            'fecha_envio':    now,
            'enviado_por_id': self.env.user.id,
        })
        self.message_post(
            body=Markup(
                '<b>Comprobante enviado</b> al proveedor por <b>{u}</b>.'
            ).format(u=self.env.user.name),
            attachment_ids=[att.id],
        )

    def action_marcar_enviado(self):
        self.ensure_one()
        if self.state != 'borrador':
            raise UserError('Solo se puede marcar como enviado un comprobante en estado Borrador.')
        now = fields.Datetime.now()
        self.write({
            'state':          'enviado',
            'fecha_envio':    now,
            'enviado_por_id': self.env.user.id,
        })
        self.message_post(
            body=Markup('<b>Marcado como enviado</b> por <b>{u}</b>.').format(
                u=self.env.user.name),
        )

    def action_marcar_declarado(self):
        for rec in self:
            if rec.state == 'anulado':
                raise UserError(
                    f'El comprobante {rec.ref} está anulado y no puede declararse.'
                )
            if rec.state == 'declarado':
                continue
            now = fields.Datetime.now()
            rec.write({
                'state':            'declarado',
                'declarado_por_id': self.env.user.id,
                'fecha_declaracion': now,
            })

    def action_anular(self):
        self.ensure_one()
        if self.state == 'declarado':
            raise UserError('No se puede anular un comprobante ya declarado.')
        self.write({'state': 'anulado'})
        self.message_post(
            body=Markup('<b>Comprobante anulado</b> por <b>{u}</b>.').format(
                u=self.env.user.name),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

    def action_imprimir_comprobante(self):
        self.ensure_one()
        return self.env.ref(
            've_retencion_iva.action_report_comprobante_prov_retencion'
        ).report_action(self)

    def action_abrir_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }
