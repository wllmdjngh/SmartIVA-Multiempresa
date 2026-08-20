from odoo import models, fields


class VeDeclaradoMensual(models.Model):
    _name = 've.declarado.mensual'
    _description = 'Monto Declarado al SENIAT por mes (carga manual del contador)'
    _order = 'anio desc, mes desc'
    _rec_name = 'periodo'

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company)
    anio = fields.Integer(string='Año', required=True)
    mes = fields.Integer(string='Mes (1-12)', required=True)
    periodo = fields.Char(string='Período', compute='_compute_periodo', store=True)
    monto_declarado = fields.Float(
        string='Monto Declarado (Bs)', digits=(16, 2),
        help='Monto que el cliente realmente declaró ante el SENIAT ese mes '
             '-- dato manual, no se calcula desde el estado de las '
             'retenciones en Odoo.')

    _periodo_company_uniq = models.Constraint(
        'unique(company_id, anio, mes)',
        'Ya existe un monto declarado para ese mes en esta compañía.',
    )

    def _compute_periodo(self):
        for rec in self:
            rec.periodo = f'{rec.anio:04d}-{rec.mes:02d}' if rec.anio and rec.mes else False
