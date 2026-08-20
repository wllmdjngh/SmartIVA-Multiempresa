import logging
import re

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

_IMG_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.webp'}
_IMG_MIME = {
    'application/pdf', 'image/jpeg', 'image/jpg', 'image/png',
    'image/tiff', 'image/bmp', 'image/webp',
}


class DiscussChannelWhatsAppHook(models.Model):
    """Detecta mensajes WhatsApp entrantes con adjuntos y los enruta al Buzón de Comprobantes."""
    _inherit = 'discuss.channel'

    def _message_post_after_hook(self, message, msg_vals):
        super()._message_post_after_hook(message, msg_vals)
        try:
            with self.env.cr.savepoint():
                self._ve_route_whatsapp_attachment(message)
        except Exception as exc:
            _logger.exception(
                've_retencion_iva WhatsApp hook error (no afecta el mensaje): %s', exc)

    def _ve_route_whatsapp_attachment(self, message):
        """Enruta adjuntos PDF/imagen de canales WhatsApp al Buzón de Comprobantes."""
        _logger.info(
            've_retencion_iva WA-hook: channel_type=%r message_type=%r '
            'attachments=%s',
            getattr(self, 'channel_type', 'N/A'),
            getattr(message, 'message_type', 'N/A'),
            [(a.name, a.mimetype) for a in message.attachment_ids] if message.attachment_ids else [],
        )
        if getattr(self, 'channel_type', None) != 'whatsapp':
            return
        if not message.attachment_ids:
            return

        # Aceptar por extensión O por mimetype (WhatsApp no siempre incluye extensión)
        img_atts = message.attachment_ids.filtered(
            lambda a: (
                any(a.name.lower().endswith(e) for e in _IMG_EXT)
                or (a.mimetype or '').lower().split(';')[0].strip() in _IMG_MIME
            )
        )
        if not img_atts:
            _logger.info(
                've_retencion_iva WA-hook: sin adjuntos imagen/PDF — '
                'names=%s mimetypes=%s',
                [a.name for a in message.attachment_ids],
                [a.mimetype for a in message.attachment_ids],
            )
            return

        # En WhatsApp dedicado a comprobantes no filtramos por keywords:
        # cualquier imagen/PDF que llegue es un comprobante.

        # Obtener datos del contacto que envió
        partner = (
            getattr(self, 'whatsapp_partner_id', False)
            or (self.channel_member_ids.mapped('partner_id')
                - self.env.user.partner_id)[:1]
        )
        phone = ''
        name  = self.name or 'WhatsApp'
        if partner:
            phone = (getattr(partner, 'mobile', None)
                     or getattr(partner, 'phone', None)
                     or getattr(self, 'whatsapp_number', None)
                     or '')
            name  = partner.name or name

        # Crear registro en el Buzón de Comprobantes
        inbox = self.env['ve.comprobante.inbox'].create({
            'canal_origen': 'whatsapp',
            'email_from':   phone or name,
            'email_asunto': f'WhatsApp: {name}',
            'fecha_email':  message.date or fields.Datetime.now(),
        })

        # Copiar adjuntos al buzón (mantener el original en el canal)
        for att in img_atts:
            att.copy({
                'res_model': 've.comprobante.inbox',
                'res_id':    inbox.id,
            })

        _logger.info(
            've.comprobante.inbox WhatsApp: creado id=%d desde %s (%s) con %d adjunto(s)',
            inbox.id, name, phone, len(img_atts),
        )

        # Procesar OCR automáticamente
        try:
            inbox._do_procesar()
        except Exception as exc:
            _logger.exception(
                've.comprobante.inbox WhatsApp auto-process id=%d: %s', inbox.id, exc)
