from markupsafe import Markup, escape
from odoo import models, fields
from odoo.exceptions import UserError


class VeWizardDeclararProv(models.TransientModel):
    _name = 've.wizard.declarar.prov'
    _description = 'Confirmar Declaración IVA Proveedores'

    declaracion_iva_id = fields.Many2one(
        've.declaracion.iva', required=True, readonly=True,
        string='Declaración IVA',
    )
    nro_declaracion = fields.Char('N° Planilla de Pago SENIAT')
    planilla_declaracion = fields.Binary('Planilla SENIAT (PDF)')
    planilla_declaracion_nombre = fields.Char()
    declarado_por_id = fields.Many2one(
        'res.users', string='Declarado por',
        default=lambda self: self.env.user,
    )
    fecha_declaracion = fields.Datetime(
        string='Fecha',
        default=lambda self: fields.Datetime.now(),
    )

    def action_confirmar(self):
        self.ensure_one()
        if not self.nro_declaracion:
            raise UserError('El N° Planilla de Pago es obligatorio para confirmar.')
        if not self.nro_declaracion.isdigit() or len(self.nro_declaracion) != 10:
            raise UserError(
                f'El N° Planilla de Pago debe tener exactamente 10 dígitos numéricos '
                f'(recibido: "{self.nro_declaracion}", {len(self.nro_declaracion)} caracteres).'
            )
        decl = self.declaracion_iva_id
        activos = decl.wh_iva_prov_ids.filtered(lambda r: r.state != 'anulado')
        if not activos:
            raise UserError(
                'No hay comprobantes activos de retención a proveedores en esta declaración.'
            )
        fecha = self.fecha_declaracion or fields.Datetime.now()
        user = self.declarado_por_id or self.env.user

        att_id = False
        planilla = False
        nombre = False
        if self.planilla_declaracion:
            nombre = self.planilla_declaracion_nombre or 'declaracion_iva_prov.pdf'
            planilla = self.planilla_declaracion
            att = self.env['ir.attachment'].create({
                'name': nombre,
                'datas': self.planilla_declaracion,
                'res_model': 've.declaracion.iva',
                'res_id': decl.id,
            })
            att_id = att.id

        decl.action_marcar_prov_declarado(
            nro=self.nro_declaracion,
            fecha=fecha,
            user=user,
            planilla=planilla,
            nombre=nombre,
        )
        if att_id:
            decl.message_post(attachment_ids=[att_id])

        return {'type': 'ir.actions.act_window_close'}
