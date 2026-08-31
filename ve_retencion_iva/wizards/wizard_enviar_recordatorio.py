from odoo import models, fields, api


class VeEnviarRecordatorioWizard(models.TransientModel):
    _name = 've.enviar.recordatorio.wizard'
    _description = 'Confirmar Envío de Recordatorio'

    wh_iva_id = fields.Many2one('ve.wh.iva', required=True, string='Comprobante')
    tipo = fields.Selection([
        ('envio_comp', 'Envío de Comprobante'),
        ('dif_seniat', 'Diferencia con SENIAT'),
        ('rep_seniat', 'Reporte a SENIAT'),
    ], required=True, string='Tipo de Recordatorio')
    partner_id = fields.Many2one(related='wh_iva_id.partner_id', readonly=True, string='Empresa')
    # Persona de contacto DENTRO de la empresa — mismo criterio que
    # wizard_registrar_llamada.py (pedido explícito 2026-08-01): antes se
    # leía/escribía directo en partner_id (la empresa), sin noción de "a
    # quién le mandé esto". Si la empresa ya tiene contactos, elegir uno;
    # si no, se completan los datos abajo y se crea un contacto nuevo.
    contacto_id = fields.Many2one(
        'res.partner', string='Contacto',
        domain="[('parent_id', '=', partner_id)]",
        help='Persona de contacto en esta empresa. Si ya existe, selecciónala '
             '— sus datos se actualizan con lo que edites abajo. Si no hay '
             'ninguna o es alguien nuevo, deja esto vacío y completa Nombre/'
             'Teléfono/Email — se crea un contacto nuevo dentro de la '
             'empresa al confirmar.')
    contacto_nombre = fields.Char(string='Nombre del Contacto')
    phone = fields.Char(string='Teléfono')
    email = fields.Char(string='Email')
    asunto = fields.Char(string='Asunto', readonly=True)
    mensaje = fields.Text(string='Mensaje', help='Puede modificar el texto antes de enviarlo.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        wh_iva_id = res.get('wh_iva_id')
        tipo = res.get('tipo')
        if wh_iva_id and tipo:
            wh = self.env['ve.wh.iva'].browse(wh_iva_id)
            asunto, _label, cuerpo = wh._generar_recordatorio_asunto_cuerpo(tipo)
            res['asunto'] = asunto
            res['mensaje'] = cuerpo
        if wh_iva_id:
            wh = self.env['ve.wh.iva'].browse(wh_iva_id)
            contactos = self.env['res.partner'].search([('parent_id', '=', wh.partner_id.id)])
            if len(contactos) == 1:
                res['contacto_id'] = contactos.id
                res['contacto_nombre'] = contactos.name
                res['phone'] = contactos.phone or getattr(contactos, 'mobile', False)
                res['email'] = contactos.email
            else:
                res.setdefault('email', wh.partner_id.email)
        return res

    @api.onchange('contacto_id')
    def _onchange_contacto_id(self):
        if self.contacto_id:
            self.contacto_nombre = self.contacto_id.name
            self.phone = self.contacto_id.phone or getattr(self.contacto_id, 'mobile', False)
            self.email = self.contacto_id.email

    def _resolver_contacto(self):
        """Ver mismo método en wizard_registrar_llamada.py."""
        self.ensure_one()
        if self.contacto_id:
            vals = {}
            if self.contacto_nombre and self.contacto_nombre != self.contacto_id.name:
                vals['name'] = self.contacto_nombre
            if self.phone != self.contacto_id.phone:
                vals['phone'] = self.phone
            if self.email != self.contacto_id.email:
                vals['email'] = self.email
            # Si el contacto viene del "Crear" rápido del propio selector
            # (el usuario escribió el nombre directo en el campo Contacto
            # en vez de dejarlo vacío) Odoo lo crea sin parent_id -- el
            # domain del campo no se aplica al quick-create. Re-vincularlo
            # acá cubre ese caso (y cualquier otro contacto ya existente
            # que no estuviera bajo esta empresa) sin depender de que el
            # usuario use el flujo "correcto".
            if self.contacto_id.parent_id != self.partner_id:
                vals['parent_id'] = self.partner_id.id
                vals['company_type'] = 'person'
                vals['company_id'] = self.partner_id.company_id.id
            if vals:
                self.contacto_id.write(vals)
            return self.contacto_id
        if self.contacto_nombre or self.phone or self.email:
            return self.env['res.partner'].create({
                'name': self.contacto_nombre or f'Contacto de {self.partner_id.name}',
                'parent_id': self.partner_id.id,
                'company_type': 'person',
                'phone': self.phone or False,
                'email': self.email or False,
                'company_id': self.partner_id.company_id.id,
            })
        return self.env['res.partner']

    def action_confirmar(self):
        self.ensure_one()
        contacto = self._resolver_contacto()
        # El campo "email" del wizard (editable, precargado del contacto
        # elegido o de la empresa si no hay contacto único) es el que
        # realmente se usa para enviar — mismo valor que ve el usuario en
        # el formulario y el aviso "sin correo configurado" de la vista.
        notificacion = self.wh_iva_id._enviar_recordatorio_tipo(
            self.tipo, cuerpo_override=self.mensaje, email_override=self.email,
            contacto_nombre=contacto.name if contacto else False)
        # Si se abrió desde la Lista de Trabajo del Dashboard, act_window_close
        # no basta (lista_trabajo_ids es computado/no almacenado, ver mismo
        # comentario en wizard_registrar_llamada.py). Historial completo de
        # intentos en wizard_registrar_llamada.py::action_registrar -- acá se
        # aplica el mismo enfoque actual: 'next' con la acción COMPLETA de
        # action_open_dashboard_operativo() (incluye 'views', a diferencia
        # del dict manual de 2026-07-15 que causó el crash documentado).
        # Pendiente de confirmar en vivo.
        if self.env.context.get('ve_desde_lista_trabajo'):
            notificacion['params']['next'] = self.env['ve.dashboard.iva'].action_open_dashboard_operativo()
        else:
            notificacion['params']['next'] = {'type': 'ir.actions.act_window_close'}
        return notificacion
