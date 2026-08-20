from odoo import models, fields
from odoo.exceptions import UserError

_MESES = [
    ('01', 'Enero'), ('02', 'Febrero'), ('03', 'Marzo'), ('04', 'Abril'),
    ('05', 'Mayo'), ('06', 'Junio'), ('07', 'Julio'), ('08', 'Agosto'),
    ('09', 'Septiembre'), ('10', 'Octubre'), ('11', 'Noviembre'), ('12', 'Diciembre'),
]


class WizardCrearPeriodo(models.TransientModel):
    _name = 've.periodo.wizard.crear'
    _description = 'Crear Nuevo Período de Conciliación SENIAT'

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company)
    anio = fields.Integer(
        string='Año', required=True,
        default=lambda self: fields.Date.today().year)
    mes = fields.Selection(
        _MESES, string='Mes', required=True,
        default=lambda self: f'{fields.Date.today().month:02d}')
    quincena = fields.Selection([
        ('1Q', '1ra Quincena (1-15)'),
        ('2Q', '2da Quincena (16-fin de mes)'),
    ], string='Quincena', required=True, default='1Q')

    def action_crear(self):
        self.ensure_one()
        Periodo = self.env['ve.conciliacion.periodo']
        periodo_retencion = f'{self.anio:04d}-{self.mes} {self.quincena}'

        existente = Periodo.search([
            ('company_id', '=', self.company_id.id),
            ('periodo_retencion', '=', periodo_retencion),
        ], limit=1)
        if existente:
            estados = dict(existente._fields['estado'].selection)
            raise UserError(
                f'Ya existe el período "{periodo_retencion}" para '
                f'{self.company_id.name} (estado: '
                f'{estados.get(existente.estado, existente.estado)}).'
            )

        dia = 1 if self.quincena == '1Q' else 16
        fecha_ref = f'{self.anio:04d}-{self.mes}-{dia:02d}'
        nuevo = Periodo._asegurar_periodo(self.company_id, fecha_ref)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 've.conciliacion.periodo',
            'res_id': nuevo.id,
            'view_mode': 'form',
            'target': 'current',
        }
