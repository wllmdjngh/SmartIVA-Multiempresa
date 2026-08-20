from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class VeSancionIva(models.Model):
    _name = 've.sancion.iva'
    _description = 'Sanción / Multa IVA Venezuela'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'fecha desc'

    name = fields.Char(string='Descripción', required=True, tracking=True)
    numero_resolucion = fields.Char(string='N° Resolución SENIAT', tracking=True)
    fecha = fields.Date(
        string='Fecha Resolución', required=True,
        default=fields.Date.today, tracking=True)
    tipo_origen = fields.Selection([
        ('periodo_especifico', 'Período Específico'),
        ('auditoria_fiscal',   'Auditoría Fiscal'),
        ('autoliquidacion',    'Autoliquidación'),
    ], string='Tipo de Origen', required=True,
        default='periodo_especifico', tracking=True)
    es_agente_retencion = fields.Boolean(
        string='Agente de Retención SPE',
        default=True, tracking=True,
        help='Empresa calificada como Agente SPE → prescripción 6 años (Art. 62 COT). '
             'Contribuyente ordinario → 10 años.')
    fecha_prescripcion = fields.Date(
        string='Prescripción',
        compute='_compute_fecha_prescripcion', store=True,
        help='Art. 62 COT: 6 años si agente de retención, 10 si contribuyente ordinario.')
    estado = fields.Selection([
        ('pendiente',  'Pendiente'),
        ('impugnada',  'Impugnada / Recurso'),
        ('pagada',     'Pagada'),
        ('prescrita',  'Prescrita'),
    ], string='Estado', required=True, default='pendiente', tracking=True)
    fecha_vencimiento_pago = fields.Date(string='Vencimiento de Pago', tracking=True)
    fecha_pago = fields.Date(string='Fecha de Pago', tracking=True)
    monto_total_bs = fields.Float(
        string='Total (Bs)', digits=(16, 2),
        compute='_compute_totales', store=True)
    monto_total_eur = fields.Float(
        string='Total (EUR)', digits=(16, 2),
        compute='_compute_totales', store=True)
    line_ids = fields.One2many('ve.sancion.iva.line', 'sancion_id', string='Líneas de Detalle')
    note = fields.Text(string='Notas / Recurso / Fundamento Legal')

    @api.depends('fecha', 'es_agente_retencion')
    def _compute_fecha_prescripcion(self):
        for rec in self:
            if rec.fecha:
                years = 6 if rec.es_agente_retencion else 10
                rec.fecha_prescripcion = rec.fecha + relativedelta(years=years)
            else:
                rec.fecha_prescripcion = False

    @api.depends('line_ids.monto_bs', 'line_ids.monto_eur')
    def _compute_totales(self):
        for rec in self:
            rec.monto_total_bs = sum(rec.line_ids.mapped('monto_bs'))
            rec.monto_total_eur = sum(rec.line_ids.mapped('monto_eur'))

    @api.model
    def _tasa_bcv_mayor_valor(self):
        """Tasa BCV de la 'moneda de mayor valor publicada por el BCV'
        (Art. 96 COT: se calcula a la tasa más favorable al fisco cuando
        hay varias tasas de referencia; reforzado por Art. 98 y Art. 10).
        La norma NO fija cuál moneda usar — compara EUR y USD del BCV y
        toma la más alta en vez de asumir EUR fijo (hoy suele ser EUR
        porque cotiza por encima del USD oficial, pero eso puede cambiar).
        Devuelve (tasa, código_moneda); (0.0, '') si no hay tasas cargadas.
        """
        Rate = self.env['res.currency.rate']
        mejor = (0.0, '')
        for code in ('EUR', 'USD'):
            rate = Rate.search([
                ('currency_id.name', '=', code),
                ('company_id', '=', self.env.company.id),
            ], order='name desc', limit=1)
            if rate and rate.rate and rate.rate > mejor[0]:
                mejor = (rate.rate, code)
        return mejor


class VeSancionIvaLine(models.Model):
    _name = 've.sancion.iva.line'
    _description = 'Línea de Sanción IVA Venezuela'

    sancion_id = fields.Many2one(
        've.sancion.iva', required=True, ondelete='cascade', index=True)
    periodo_id = fields.Many2one(
        've.conciliacion.periodo', string='Período',
        help='Período de conciliación afectado (opcional).')
    periodo_desc = fields.Char(
        string='Período (descripción)',
        help='Texto libre si no existe un período de conciliación (ej. "2024 1Q").')
    tipo = fields.Selection([
        ('ilicito_formal',    'Ilícito Formal — Art. 101 COT'),
        ('omision',           'Omisión de Pago — Art. 111 COT'),
        ('rechazo_credito',   'Rechazo Crédito Fiscal — Art. 94 COT'),
        ('interes_moratorio', 'Interés Moratorio — Art. 66 COT'),
        ('multa_forma',       'Multa Formal — Art. 100 COT'),
    ], string='Tipo', required=True, default='ilicito_formal')
    descripcion = fields.Char(string='Descripción')
    cantidad = fields.Integer(string='Cantidad', default=1,
                              help='N° de comprobantes / períodos afectados (referencial).')
    monto_bs = fields.Float(
        string='Monto Bs (a la resolución)', digits=(16, 2),
        help='Monto en bolívares al momento de la resolución/registro. '
             'Es el ancla histórica de la que se deriva el monto en EUR '
             '(el monto legal fijo). El Bs se actualiza con la tasa '
             'vigente hasta el día del pago — ver "Monto Bs (hoy)".')
    tasa_eur_bcv = fields.Float(
        string='Tasa EUR/BCV (Bs por EUR)', digits=(16, 4),
        help='Tasa del BCV al momento del registro. Se usa para convertir a EUR.')
    monto_eur = fields.Float(
        string='Monto (EUR)', digits=(16, 2),
        compute='_compute_monto_eur', store=True)
    monto_bs_hoy = fields.Float(
        string='Monto Bs (hoy)', digits=(16, 2),
        compute='_compute_monto_bs_hoy', store=False,
        help='Monto en Bs recalculado con la tasa BCV de la moneda de '
             'mayor valor vigente HOY (Art. 96/98/108 COT: la multa se '
             'fija en divisa, se paga en Bs al tipo de cambio del día). '
             'Una vez la sanción pasa a Pagada, queda igual al Monto Bs '
             'de la resolución (se congela a la tasa del día de pago).')
    wh_iva_ids = fields.Many2many(
        've.wh.iva', string='Retenciones Vinculadas',
        help='Comprobantes de retención relacionados con esta sanción.')

    @api.depends('monto_bs', 'tasa_eur_bcv')
    def _compute_monto_eur(self):
        for rec in self:
            if rec.tasa_eur_bcv and rec.tasa_eur_bcv > 0:
                rec.monto_eur = rec.monto_bs / rec.tasa_eur_bcv
            else:
                rec.monto_eur = 0.0

    @api.depends()
    def _compute_monto_bs_hoy(self):
        tasa_hoy, _moneda = self.env['ve.sancion.iva']._tasa_bcv_mayor_valor()
        for rec in self:
            ya_pagada = rec.sancion_id.estado == 'pagada'
            if ya_pagada or not tasa_hoy:
                rec.monto_bs_hoy = rec.monto_bs
            else:
                rec.monto_bs_hoy = round(rec.monto_eur * tasa_hoy, 2)

    @api.onchange('tipo')
    def _onchange_tipo_tasa(self):
        if not self.tasa_eur_bcv:
            rate = self.env['res.currency.rate'].search([
                ('currency_id.name', '=', 'EUR'),
                ('company_id', '=', self.env.company.id),
            ], order='name desc', limit=1)
            if rate and rate.rate:
                self.tasa_eur_bcv = rate.rate
