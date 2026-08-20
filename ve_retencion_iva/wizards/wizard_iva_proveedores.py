from odoo import models, fields


class VeIvaProveedoresPlaceholder(models.TransientModel):
    _name = 've.iva.proveedores.placeholder'
    _description = 'IVA Proveedores — No disponible'

    message = fields.Text(
        string='Aviso',
        default='Esta opción no está habilitada por ahora.',
        readonly=True,
    )
