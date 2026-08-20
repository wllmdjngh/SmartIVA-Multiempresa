from odoo import models, fields
from odoo.exceptions import UserError


class VeWizardDeclarar(models.TransientModel):
    _name = 've.wizard.declarar'
    _description = 'Confirmar Declaración SENIAT'

    declaracion_iva_id = fields.Many2one(
        've.declaracion.iva', required=True, readonly=True,
        string='Declaración IVA',
    )
    nro_declaracion = fields.Char('N° Certificado SENIAT')
    planilla_declaracion = fields.Binary('Planilla SENIAT (PDF)')
    planilla_declaracion_nombre = fields.Char()

    def action_confirmar(self):
        self.ensure_one()
        if not self.nro_declaracion:
            raise UserError('El N° Certificado SENIAT es obligatorio para confirmar.')
        if not self.nro_declaracion.isdigit() or len(self.nro_declaracion) != 21:
            raise UserError(
                f'El N° Certificado SENIAT debe tener exactamente 21 dígitos numéricos '
                f'(recibido: "{self.nro_declaracion}", {len(self.nro_declaracion)} caracteres).'
            )
        decl = self.declaracion_iva_id
        vals = {'nro_declaracion': self.nro_declaracion}
        if self.planilla_declaracion:
            vals['planilla_declaracion'] = self.planilla_declaracion
            vals['planilla_declaracion_nombre'] = (
                self.planilla_declaracion_nombre or 'declaracion_seniat.pdf'
            )
        decl.write(vals)
        decl.action_marcar_declarado()
        return {'type': 'ir.actions.act_window_close'}
