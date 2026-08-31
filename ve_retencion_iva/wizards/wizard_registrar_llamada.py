from markupsafe import Markup, escape
from odoo import api, models, fields


class VeRegistrarLlamadaWizard(models.TransientModel):
    _name = 've.registrar.llamada.wizard'
    _description = 'Registrar Llamada a Contacto'

    wh_iva_id = fields.Many2one('ve.wh.iva', required=True, string='Comprobante')
    partner_id = fields.Many2one(related='wh_iva_id.partner_id', readonly=True, string='Empresa')
    # Persona de contacto DENTRO de la empresa — antes el wizard leía/
    # escribía directo en partner_id (la empresa), sin noción de "con
    # quién hablé". Pedido explícito 2026-08-01: si la empresa ya tiene
    # contactos (res.partner con parent_id = esta empresa), elegir uno de
    # la lista; si no hay ninguno o es una persona nueva, se completan los
    # datos abajo y se crea un contacto nuevo al registrar.
    contacto_id = fields.Many2one(
        'res.partner', string='Contacto',
        domain="[('parent_id', '=', partner_id)]",
        help='Persona de contacto en esta empresa. Si ya existe, selecciónala '
             '— sus datos se actualizan con lo que edites abajo. Si no hay '
             'ninguna o es alguien nuevo, deja esto vacío y completa Nombre/'
             'Teléfono/Email — se crea un contacto nuevo dentro de la '
             'empresa al registrar.')
    contacto_nombre = fields.Char(string='Nombre del Contacto')
    phone = fields.Char(string='Teléfono')
    email = fields.Char(string='Email')
    nota = fields.Text(string='Motivo / Resultado de la llamada')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        wh_iva_id = res.get('wh_iva_id')
        if wh_iva_id:
            wh = self.env['ve.wh.iva'].browse(wh_iva_id)
            contactos = self.env['res.partner'].search([('parent_id', '=', wh.partner_id.id)])
            # Solo se auto-selecciona si hay UNA sola opción — con 2+
            # contactos, que el usuario elija a propósito en vez de
            # asumir cuál es el correcto.
            if len(contactos) == 1:
                res['contacto_id'] = contactos.id
                res['contacto_nombre'] = contactos.name
                res['phone'] = contactos.phone or getattr(contactos, 'mobile', False)
                res['email'] = contactos.email
        return res

    @api.onchange('contacto_id')
    def _onchange_contacto_id(self):
        if self.contacto_id:
            self.contacto_nombre = self.contacto_id.name
            self.phone = self.contacto_id.phone or getattr(self.contacto_id, 'mobile', False)
            self.email = self.contacto_id.email

    def _resolver_contacto(self):
        """Escribe los datos (posiblemente editados) en el contacto
        elegido, o crea uno nuevo dentro de la empresa si no se eligió
        ninguno — ya NO toca partner_id (la empresa) directamente."""
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

    def action_registrar(self):
        self.ensure_one()
        contacto = self._resolver_contacto()
        ahora = fields.Datetime.now()
        usuario = self.env.user.name
        numero = self.phone or '—'
        nombre_contacto = contacto.name if contacto else False
        cuerpo = Markup(
            '<b>Llamada telef&#243;nica</b> a <b>{empresa}</b>{contacto} '
            '(&#9742; {numero}) el {fecha}, registrada por <b>{usuario}</b>.{nota}'
        ).format(
            empresa=escape(self.partner_id.name or 'cliente'),
            contacto=Markup(' — contacto: <b>{}</b>').format(escape(nombre_contacto))
                if nombre_contacto else Markup(''),
            numero=escape(numero),
            fecha=ahora.strftime('%d/%m/%Y %H:%M'),
            usuario=escape(usuario),
            nota=Markup('<br/><i>{}</i>').format(escape(self.nota)) if self.nota else Markup(''),
        )
        self.wh_iva_id.message_post(
            body=cuerpo,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        self.wh_iva_id.fecha_ultima_llamada = ahora
        # 'ir.actions.act_window_close' NO basta cuando se abre desde la
        # Lista de Trabajo del Dashboard: lista_trabajo_ids (ve_dashboard_iva.py)
        # es un campo computado, no almacenado -- cerrar el wizard solo
        # refresca los valores de las filas ya conocidas, nunca vuelve a
        # ejecutar el cómputo que decide qué retenciones aparecen.
        #
        # Intentos previos documentados en este mismo archivo/commits:
        #   1) 2026-07-15 (621d147..da6a512): 'next' anidado con un dict
        #      MANUAL incompleto ({'view_mode': 'form'} sin 'views': [...])
        #      -- crasheaba _preprocessAction con "Cannot read properties
        #      of undefined (reading 'map')" (revertido en 50b4cb8, demo).
        #   2) 2026-08-25 v19.0.2.14.148: 'reload' anidado en next -- no
        #      confirmado si funcionaba (se probó junto a una regresión de
        #      CSS que rompía la columna Cliente, imposible de aislar).
        #   3) 2026-08-25 v19.0.2.14.150/151: 'reload' como retorno directo
        #      (sin anidar) -- reportado que tampoco refrescó.
        # Intento actual: notificación + 'next' con la acción COMPLETA que
        # arma action_open_dashboard_operativo() (incluye 'views', el campo
        # que le faltaba al dict de 2026-07-15) -- pendiente de confirmar
        # en vivo, no se pudo verificar visualmente esta sesión (el
        # navegador de pruebas no logró montar el cliente web de Odoo).
        if self.env.context.get('ve_desde_lista_trabajo'):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Llamada registrada',
                    'message': f'Se registró la llamada a {self.partner_id.name or "cliente"} en la bitácora',
                    'type': 'success',
                    'sticky': False,
                    'next': self.env['ve.dashboard.iva'].action_open_dashboard_operativo(),
                },
            }
        return {'type': 'ir.actions.act_window_close'}
