from odoo import models, fields


class VeConciliarConfirmWizard(models.TransientModel):
    _name = 've.conciliar.confirm.wizard'
    _description = 'Confirmar Conciliación con Retenciones Recibidas sin Confirmar'

    conciliacion_id = fields.Many2one('ve.conciliacion.periodo', required=True)
    n_borrador = fields.Integer(string='Retenciones en estado Recibido', readonly=True)

    def _return_conciliacion(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 've.conciliacion.periodo',
            'res_id': self.conciliacion_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_proceder(self):
        """Concilia sin confirmar los Recibidos pendientes."""
        self.conciliacion_id._do_conciliar()
        return self._return_conciliacion()
