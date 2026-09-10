# -*- coding: utf-8 -*-
"""Calendario SENIAT real de vencimientos para Sujetos Pasivos Especiales /
Agentes de Retención (C1, ver [[project_pendientes_codigo_pre_piloto_vencement]]
item 5). SENIAT publica una Providencia Administrativa cada año con 2 tablas
(retenciones practicadas 01-15 / 16-fin de cada mes) x 10 filas (último
dígito del RIF) x 12 columnas (mes del período) -- el día de vencimiento
real varía por corrimiento de fines de semana/feriados, no es un offset fijo.

Dato GLOBAL, no por compañía -- una sola carga anual sirve para TODAS las
compañías de una base multiempresa, cada una consulta con su propio RIF."""

from odoo import api, fields, models
from odoo.exceptions import UserError


class VeCalendarioSeniat(models.Model):
    _name = 've.calendario.seniat'
    _description = 'Calendario SENIAT — Vencimientos SPE/Agentes de Retención'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'anio desc'
    _rec_name = 'name'

    name = fields.Char(string='Referencia', compute='_compute_name', store=True)
    anio = fields.Integer(string='Año Calendario', required=True, tracking=True)
    providencia = fields.Char(
        string='N° Providencia', tracking=True,
        help='Ej. SNAT/2025/000091 — la Providencia Administrativa que '
             'publica este calendario.')
    gaceta_oficial = fields.Char(string='N° Gaceta Oficial', tracking=True)
    fecha_gaceta = fields.Date(string='Fecha Gaceta/Providencia', tracking=True)
    archivo = fields.Binary(
        string='Providencia (PDF/Imagen)', attachment=True,
        help='Se adjunta también al chatter para dejar registro de la '
             'fuente oficial exacta usada.')
    archivo_nombre = fields.Char(string='Nombre del Archivo')
    estado = fields.Selection([
        ('borrador', 'Borrador'),
        ('confirmado', 'Confirmado'),
    ], string='Estado', default='borrador', tracking=True)
    linea_ids = fields.One2many(
        've.calendario.seniat.linea', 'calendario_id', string='Días Límite')
    count_lineas = fields.Integer(compute='_compute_count_lineas')

    _sql_constraints = [
        ('anio_uniq', 'unique(anio)',
         'Ya existe un calendario cargado para ese año.'),
    ]

    @api.depends('anio', 'providencia')
    def _compute_name(self):
        for rec in self:
            rec.name = f'CALENDARIO-SENIAT/{rec.anio or "?"}'

    @api.depends('linea_ids')
    def _compute_count_lineas(self):
        for rec in self:
            rec.count_lineas = len(rec.linea_ids)

    def action_confirmar(self):
        for rec in self:
            if len(rec.linea_ids) != 240:
                raise UserError(
                    f'Se esperan 240 filas (2 quincenas × 12 meses × 10 '
                    f'dígitos de RIF) — hay {len(rec.linea_ids)}. Revise '
                    f'antes de confirmar.')
            rec.estado = 'confirmado'

    @api.model
    def _fecha_limite_seniat(self, company, fecha_inicio):
        """Fecha límite real de declaración/pago SENIAT para un período que
        empieza en fecha_inicio, según el RIF de `company`. Devuelve None si
        no hay calendario cargado para ese año (el caller debe usar su
        propio fallback -- no se asume ningún comportamiento por defecto
        acá, para que cada call site decida su propia red de seguridad)."""
        if not fecha_inicio or not company or not company.vat:
            return None
        digitos = ''.join(c for c in company.vat if c.isdigit())
        if not digitos:
            return None
        rif_digito = int(digitos[-1])
        quincena = '1Q' if fecha_inicio.day <= 15 else '2Q'

        linea = self.env['ve.calendario.seniat.linea'].search([
            ('calendario_id.anio', '=', fecha_inicio.year),
            ('calendario_id.estado', '=', 'confirmado'),
            ('quincena', '=', quincena),
            ('mes', '=', fecha_inicio.month),
            ('rif_digito', '=', rif_digito),
        ], limit=1)
        if not linea:
            return None

        # 1Q vence dentro del mismo mes; 2Q vence en el mes siguiente (con
        # rollover de año si el período es diciembre).
        anio_venc = fecha_inicio.year
        mes_venc = fecha_inicio.month
        if quincena == '2Q':
            mes_venc += 1
            if mes_venc > 12:
                mes_venc = 1
                anio_venc += 1
        return fields.Date.from_string(
            f'{anio_venc:04d}-{mes_venc:02d}-{linea.dia_limite:02d}')


class VeCalendarioSeniatLinea(models.Model):
    _name = 've.calendario.seniat.linea'
    _description = 'Calendario SENIAT — Día Límite por RIF/Mes/Quincena'
    _order = 'quincena, mes, rif_digito'

    calendario_id = fields.Many2one(
        've.calendario.seniat', required=True, ondelete='cascade', index=True)
    quincena = fields.Selection([
        ('1Q', 'Entre 01 y 15'),
        ('2Q', 'Entre 16 y fin de mes'),
    ], required=True)
    mes = fields.Integer(string='Mes del Período', required=True)
    rif_digito = fields.Integer(string='Último Dígito RIF', required=True)
    dia_limite = fields.Integer(string='Día Límite', required=True)

    _sql_constraints = [
        ('linea_uniq', 'unique(calendario_id, quincena, mes, rif_digito)',
         'Ya existe una fila para esa quincena/mes/dígito en este calendario.'),
    ]
