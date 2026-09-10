# -*- coding: utf-8 -*-
"""Calendario SENIAT real de vencimientos para Sujetos Pasivos Especiales /
Agentes de Retención (C1, ver [[project_pendientes_codigo_pre_piloto_vencement]]
item 5). SENIAT publica una Providencia Administrativa cada año con 2 tablas
(retenciones practicadas 01-15 / 16-fin de cada mes) x 10 filas (último
dígito del RIF) x 12 columnas (mes del período) -- el día de vencimiento
real varía por corrimiento de fines de semana/feriados, no es un offset fijo.

Dato GLOBAL, no por compañía -- una sola carga anual sirve para TODAS las
compañías de una base multiempresa, cada una consulta con su propio RIF."""

from odoo import api, fields, models, tools
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

    _anio_uniq = models.Constraint(
        'unique(anio)', 'Ya existe un calendario cargado para ese año.')

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

    _linea_uniq = models.Constraint(
        'unique(calendario_id, quincena, mes, rif_digito)',
        'Ya existe una fila para esa quincena/mes/dígito en este calendario.')


class VeCalendarioSeniatPorEmpresa(models.Model):
    """Vista SQL de solo lectura -- pedido explícito 2026-09-10: ver el
    calendario ya resuelto por compañía (RIF real, no dígito suelto), con
    una fila por Empresa+Quincena y una columna por mes (Ene..Dic). Siempre
    refleja el calendario CONFIRMADO más reciente que tenga esa
    combinación -- no hay que sincronizar nada, es un JOIN en vivo contra
    res.company + ve.calendario.seniat.linea."""
    _name = 've.calendario.seniat.por.empresa'
    _description = 'Calendario SENIAT por Empresa'
    _auto = False
    _order = 'company_id, anio desc, quincena'

    company_id = fields.Many2one('res.company', string='Empresa', readonly=True)
    rif = fields.Char(string='RIF', readonly=True)
    anio = fields.Integer(string='Año', readonly=True)
    quincena = fields.Selection([
        ('1Q', 'Entre 01 y 15'),
        ('2Q', 'Entre 16 y fin de mes'),
    ], readonly=True)
    mes_01 = fields.Integer(string='Ene', readonly=True)
    mes_02 = fields.Integer(string='Feb', readonly=True)
    mes_03 = fields.Integer(string='Mar', readonly=True)
    mes_04 = fields.Integer(string='Abr', readonly=True)
    mes_05 = fields.Integer(string='May', readonly=True)
    mes_06 = fields.Integer(string='Jun', readonly=True)
    mes_07 = fields.Integer(string='Jul', readonly=True)
    mes_08 = fields.Integer(string='Ago', readonly=True)
    mes_09 = fields.Integer(string='Sep', readonly=True)
    mes_10 = fields.Integer(string='Oct', readonly=True)
    mes_11 = fields.Integer(string='Nov', readonly=True)
    mes_12 = fields.Integer(string='Dic', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    row_number() OVER (ORDER BY c.id, cal.anio DESC, q.quincena) AS id,
                    c.id AS company_id,
                    c.vat AS rif,
                    cal.anio AS anio,
                    q.quincena AS quincena,
                    MAX(CASE WHEN l.mes = 1  THEN l.dia_limite END) AS mes_01,
                    MAX(CASE WHEN l.mes = 2  THEN l.dia_limite END) AS mes_02,
                    MAX(CASE WHEN l.mes = 3  THEN l.dia_limite END) AS mes_03,
                    MAX(CASE WHEN l.mes = 4  THEN l.dia_limite END) AS mes_04,
                    MAX(CASE WHEN l.mes = 5  THEN l.dia_limite END) AS mes_05,
                    MAX(CASE WHEN l.mes = 6  THEN l.dia_limite END) AS mes_06,
                    MAX(CASE WHEN l.mes = 7  THEN l.dia_limite END) AS mes_07,
                    MAX(CASE WHEN l.mes = 8  THEN l.dia_limite END) AS mes_08,
                    MAX(CASE WHEN l.mes = 9  THEN l.dia_limite END) AS mes_09,
                    MAX(CASE WHEN l.mes = 10 THEN l.dia_limite END) AS mes_10,
                    MAX(CASE WHEN l.mes = 11 THEN l.dia_limite END) AS mes_11,
                    MAX(CASE WHEN l.mes = 12 THEN l.dia_limite END) AS mes_12
                FROM res_company c
                CROSS JOIN (SELECT unnest(ARRAY['1Q', '2Q']) AS quincena) q
                JOIN ve_calendario_seniat cal ON cal.estado = 'confirmado'
                JOIN ve_calendario_seniat_linea l
                    ON l.calendario_id = cal.id
                   AND l.quincena = q.quincena
                   AND l.rif_digito = CAST(
                       right(regexp_replace(c.vat, '\\D', '', 'g'), 1) AS integer)
                WHERE c.vat IS NOT NULL
                  AND regexp_replace(c.vat, '\\D', '', 'g') != ''
                GROUP BY c.id, c.vat, cal.anio, q.quincena
            )
        """)
