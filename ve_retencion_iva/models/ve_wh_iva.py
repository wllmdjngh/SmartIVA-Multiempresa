import calendar
import logging
import re
from datetime import date, timedelta

from markupsafe import Markup, escape
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_RIF_RE = re.compile(r'^[JVEG]-\d{8}-\d$')
_NRO_COMP_RE = re.compile(r'^\d{14}$')


# ── Helpers de fechas hábiles ────────────────────────────────────────────────

def _easter_sunday(year):
    """Domingo de Pascua (algoritmo de Gauss, calendario gregoriano) — ancla
    para calcular Carnaval y Semana Santa, que se mueven cada año."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


_FERIADOS_VE_CACHE = {}


def _feriados_venezuela(year):
    """Feriados bancarios/oficiales de Venezuela para un año dado: fijos
    (Gaceta Oficial) + móviles calculados desde Pascua (Carnaval, Semana
    Santa). No incluye días no laborables decretados ad-hoc — para esos,
    ver el parámetro de sistema ve_retencion_iva.feriados_adicionales."""
    if year not in _FERIADOS_VE_CACHE:
        pascua = _easter_sunday(year)
        fijos = {
            date(year, 1, 1),    # Año Nuevo
            date(year, 4, 19),   # Declaración de la Independencia
            date(year, 5, 1),    # Día del Trabajador
            date(year, 6, 24),   # Batalla de Carabobo
            date(year, 7, 5),    # Día de la Independencia
            date(year, 7, 24),   # Natalicio de Simón Bolívar
            date(year, 10, 12),  # Día de la Resistencia Indígena
            date(year, 12, 25),  # Navidad
        }
        moviles = {
            pascua - timedelta(days=48),  # Lunes de Carnaval
            pascua - timedelta(days=47),  # Martes de Carnaval
            pascua - timedelta(days=3),   # Jueves Santo
            pascua - timedelta(days=2),   # Viernes Santo
        }
        _FERIADOS_VE_CACHE[year] = fijos | moviles
    return _FERIADOS_VE_CACHE[year]


def _es_feriado_venezuela(d, feriados_extra=frozenset()):
    return d in _feriados_venezuela(d.year) or d in feriados_extra


def _nth_business_day(start_date, n, feriados_extra=frozenset()):
    """Devuelve el n-ésimo día hábil contando desde start_date (inclusive),
    excluyendo fines de semana y feriados venezolanos."""
    count = 0
    current = start_date - timedelta(days=1)
    while count < n:
        current += timedelta(days=1)
        if current.weekday() < 5 and not _es_feriado_venezuela(current, feriados_extra):
            count += 1
    return current


def _deadline_from_invoice_date(invoice_date, feriados_extra=frozenset()):
    """Plazo legal: 2 días hábiles del inicio del período quincenal siguiente."""
    if not invoice_date:
        return None
    if invoice_date.day <= 15:                          # 1Q → siguiente inicia día 16
        period_start = date(invoice_date.year, invoice_date.month, 16)
    else:                                               # 2Q → siguiente inicia día 1 del mes siguiente
        if invoice_date.month == 12:
            period_start = date(invoice_date.year + 1, 1, 1)
        else:
            period_start = date(invoice_date.year, invoice_date.month + 1, 1)
    return _nth_business_day(period_start, 2, feriados_extra)


class VeWhIva(models.Model):
    _name = 've.wh.iva'
    _description = 'Retenciones IVA Venezuela'
    _rec_name = 'ref'
    _order = 'fecha desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    def _compute_display_name(self):
        for rec in self:
            parts = [rec.ref or f'RET#{rec.id}']
            if rec.partner_id:
                parts.append(rec.partner_id.name)
            if rec.name:
                parts.append(rec.name)
            rec.display_name = ' · '.join(parts)

    # ── Unicidad a nivel de base de datos ───────────────────────────────────
    # Bug real encontrado 2026-08-05 (Cementos): la restricción original
    # (name+partner_id+company_id) nunca se pudo crear -- 41 grupos reales
    # (122 filas) violándola, confirmado por RPC. Causa raíz: un mismo
    # N° Comprobante puede cubrir VARIAS facturas legítimamente cuando el
    # cliente agrupa varios pagos/facturas bajo un solo comprobante de
    # retención (ej. GARAM CONSTRUCTORES, 13 de los 41 grupos) -- name NO
    # es un identificador único real. invoice_id sí lo es: verificado por
    # RPC que las 530 retenciones con factura vinculada tienen invoice_id
    # único por compañía, cero conflictos. Los registros sin factura
    # (invoice_id NULL) no violan la restricción -- PostgreSQL no compara
    # NULLs como iguales en UNIQUE.
    _unique_invoice_company = models.Constraint(
        'UNIQUE(invoice_id, company_id)',
        'Ya existe una retención para esta factura en esta empresa.',
    )

    # ── Identificación ───────────────────────────────────────────────────────
    ref = fields.Char(
        string='Referencia',
        copy=False,
        readonly=True,
        help='Número interno auto-generado al crear la retención (RET/YYYY/NNNN).',
    )
    name = fields.Char(
        string='N° Comprobante',
        tracking=True,
        help='N° SENIAT de 14 dígitos. Se registra al recibir el comprobante.',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente / Agente de Retención',
        tracking=True,
        index=True,
    )
    es_agente_retencion = fields.Boolean(
        related='partner_id.es_agente_retencion',
        string='Contribuyente Especial',
        store=True,
    )
    rif = fields.Char(
        string='RIF',
        related='partner_id.vat',
        store=True,
    )
    partner_phone = fields.Char(
        string='Teléfono',
        related='partner_id.phone',
        readonly=True,
    )
    invoice_id = fields.Many2one(
        'account.move',
        string='Factura',
        domain="[('move_type', 'in', ('out_invoice', 'out_refund')), "
               " ('company_id', '=', company_id)]",
        tracking=True,
        index=True,
    )
    invoice_name = fields.Char(
        string='N° Factura',
        related='invoice_id.name',
        help='Solo el número de factura, sin la Referencia entre paréntesis '
             'que trae el widget de relación (Many2one) por defecto.',
    )

    # ── Ciclo de vida ────────────────────────────────────────────────────────
    # Etapa 4 del rediseño de 3 ejes (2026-07-24): 'conciliado'/'declarado'
    # se quitan de esta Selection — Conciliación (Eje 2) y Declaración
    # (Eje 3) viven en estado_conciliacion/estado_declaracion desde la
    # Etapa 3; `state` vuelve a ser solo el flujo real de recepción física
    # (¿llegó el papel?), máximo 'confirmado'. Migración de filas legado
    # todavía en 'conciliado'/'declarado' en migrations/19.0.2.11.9/.
    state = fields.Selection([
        ('esperado',   'No Recibido'),
        ('vencido',    'Vencido'),
        ('borrador',   'Recibido'),
        ('confirmado', 'Confirmado'),
        ('anulado',    'Anulado'),
    ], string='Estado', default='esperado', required=True, tracking=True, index=True)

    # ── Período y fechas ─────────────────────────────────────────────────────
    periodo = fields.Char(
        string='Período Fiscal',
        help='Formato: yyyy-mm. Ej: 2026-05. Se deriva de la factura o del comprobante.',
    )
    periodo_retencion = fields.Char(
        string='Período Declaración',
        compute='_compute_periodo_retencion',
        store=True,
        help='Formato: yyyy-mm 1Q ó yyyy-mm 2Q.',
    )
    fecha = fields.Date(
        string='Fecha Recibido',
        tracking=True,
    )
    fecha_vencimiento_entrega = fields.Date(
        string='Fecha Límite Entrega',
        compute='_compute_fecha_vencimiento',
        store=True,
        help='2 días hábiles del inicio del período quincenal siguiente.',
    )
    fuera_plazo = fields.Boolean(
        string='Recibido Fuera de Plazo',
        compute='_compute_fuera_plazo',
        store=True,
        tracking=True,
    )

    # ── Montos ───────────────────────────────────────────────────────────────
    monto_base = fields.Float(
        string='Base Imponible (16%)',
        digits=(16, 2),
    )
    monto_iva = fields.Float(
        string='IVA (16%)',
        digits=(16, 2),
    )
    monto_base_red = fields.Float(
        string='Base Imponible (8%)',
        digits=(16, 2),
    )
    monto_iva_red = fields.Float(
        string='IVA (8%)',
        digits=(16, 2),
    )
    porcentaje_retencion = fields.Float(
        string='% Retención',
        default=75.0,
        digits=(5, 2),
        tracking=True,
        # Sin esto, Odoo suma el % en el pie de cualquier lista que lo
        # muestre como columna (default de fields.Float es 'sum') --
        # sumar porcentajes entre filas no tiene sentido. Bug real
        # reportado 2026-08-01.
        aggregator=False,
    )
    viene_de_libro_ventas = fields.Boolean(
        default=False,
        copy=False,
        help='True si esta retención se creó desde una carga del Libro de '
             'Ventas (Conecta) -- necesario para distinguir "el archivo '
             'decía 0 retenido" (0 real, sí hay diferencia si Odoo calculó '
             'algo) de "esta retención no vino de ninguna carga" (0 porque '
             'monto_retenido_archivo nunca se llenó). Un Float en 0 no '
             'alcanza para distinguir los dos casos -- bug real encontrado '
             '2026-08-06: "Facturas con Diferencia" mostraba filas con '
             'Diferencia=0 porque el compute las trataba igual. Ver '
             '_compute_diferencia_vs_archivo.',
    )
    monto_retenido_archivo = fields.Float(
        string='Monto Retenido (Archivo)',
        digits=(16, 2),
        copy=False,
        help='Monto tal cual venía en el Libro de Ventas (Conecta) para esta '
             'factura -- solo se llena si la retención se creó desde esa '
             'carga. Sirve para comparar contra el Monto Esperado que '
             'calculó Odoo (puede diferir por reglas legales que el archivo '
             'del cliente no aplicó -- ej. 100% sin N° Control, o retención '
             'esperada aunque el archivo trajera 0). Pedido explícito '
             '2026-08-05, ver ve_conecta_carga_ventas.py::action_confirmar '
             'y action_ver_diferencias_archivo.',
    )
    diferencia_archivo_aceptada = fields.Boolean(
        default=False,
        copy=False,
        help='True cuando el usuario ya usó "Aceptar Monto del Archivo". '
             'Necesario aparte de diferencia_vs_archivo==0: si la retención '
             'no tiene N° de Control, "Facturas con Diferencia" la muestra '
             'igual (ver action_ver_diferencias_archivo) aunque el % ya se '
             'haya ajustado -- sin este flag, una retención aceptada nunca '
             'desaparecía de la lista de pendientes por esa otra causa '
             'independiente. Pedido explícito 2026-08-06.',
    )
    diferencia_vs_archivo = fields.Float(
        string='Diferencia vs. Archivo',
        digits=(16, 2),
        compute='_compute_diferencia_vs_archivo',
        store=True,
        help='Monto Esperado - Monto Retenido (Archivo). En 0 si esta '
             'retención no vino de una carga del Libro de Ventas.',
    )
    pct_diferencia_archivo = fields.Char(
        string='% Diferencia',
        compute='_compute_diferencia_vs_archivo',
        store=True,
        help='Diferencia vs. Archivo como % del Monto Retenido (Archivo). '
             'Char (no Float) a propósito: cuando el archivo trae 0 y Odoo '
             'calculó un monto, el % es indefinido (división entre 0), no '
             '0% -- mostrar "0%" ahí sugería falsamente que no había '
             'diferencia. Se muestra "Archivo=0" en ese caso en vez de '
             'inventar un número. Bug real reportado 2026-08-06.',
    )
    monto_iva_archivo = fields.Float(
        string='Monto IVA (Archivo)',
        digits=(16, 2),
        copy=False,
        help='Impuesto IVA (16%+8% combinados) tal cual venía en el Libro '
             'de Ventas para esta factura -- mismo criterio que '
             'monto_retenido_archivo, solo se llena si vino de esa carga.',
    )
    monto_iva_total = fields.Float(
        string='Monto IVA (Odoo)',
        digits=(16, 2),
        compute='_compute_totales_odoo',
        store=True,
        help='monto_iva + monto_iva_red (16%+8% combinados) -- para '
             'comparar de un vistazo contra monto_iva_archivo. El '
             'desglose por alícuota está en la ficha de la retención.',
    )
    base_imponible_total = fields.Float(
        string='Base Imponible',
        digits=(16, 2),
        compute='_compute_totales_odoo',
        store=True,
        help='monto_base + monto_base_red (16%+8% combinados). El '
             'desglose por alícuota está en la ficha de la retención.',
    )
    monto_retenido = fields.Float(
        string='Monto Esperado',
        digits=(16, 2),
        compute='_compute_monto_retenido',
        store=True,
    )
    monto_recibido = fields.Float(
        string='Monto Recibidos',
        digits=(16, 2),
        compute='_compute_monto_recibido',
        store=True,
        help='Monto retenido solo cuando el comprobante fue efectivamente recibido.',
    )
    monto_c66 = fields.Float(
        string='Monto C.66',
        digits=(16, 2),
        compute='_compute_monto_c66',
        store=True,
        help='Monto Recibidos que aplica al Campo 66 (incluir_declaracion=True).',
    )
    monto_exento = fields.Float(string='Base Exento', digits=(16, 2))
    monto_base_nogravado = fields.Float(string='Base No Gravada', digits=(16, 2))
    alicuota = fields.Float(string='Alícuota %', digits=(5, 2))

    # ── Montos según comprobante del cliente (entrada manual) ─────────────
    comp_base_16 = fields.Float(string='Base Imponible 16%', digits=(16, 2))
    comp_iva_16  = fields.Float(string='IVA 16%',            digits=(16, 2))
    comp_base_8  = fields.Float(string='Base Imponible 8%',  digits=(16, 2))
    comp_iva_8   = fields.Float(string='IVA 8%',             digits=(16, 2))
    comp_monto_retenido = fields.Float(
        string='Monto Retenido (Comp.)',
        digits=(16, 2),
        help='Monto de retención declarado en el comprobante físico del cliente.',
    )
    comp_base_exento = fields.Float(string='Base Exento (Comp.)', digits=(16, 2))
    comp_base_nogravado = fields.Float(string='Base No Gravada (Comp.)', digits=(16, 2))

    # ── Recepción ────────────────────────────────────────────────────────────
    comprobante_recibido = fields.Boolean(
        string='Comprobante Recibido',
        compute='_compute_comprobante_recibido',
        store=True,
    )
    canal_recepcion = fields.Selection([
        ('email',        'Email'),
        ('whatsapp',     'WhatsApp'),
        ('directorio',   'Directorio Compartido'),
        ('manual',       'Subida Manual'),
        ('libro_ventas', 'Carga Libro de Ventas'),
    ], string='Canal de Recepción')

    # ── Cobranza (cruce con estado de pago de la factura) ──────────────────
    payment_state = fields.Selection(
        related='invoice_id.payment_state',
        string='Estado de Pago',
        store=True,
    )
    estado_cobranza = fields.Selection([
        ('no_pagado',              'No Pagado'),
        ('pagado_con_comprobante', 'Pagado · Con Comprobante'),
        ('pagado_sin_comprobante', 'Pagado · Sin Comprobante'),
    ], string='Cobranza vs. Comprobante', compute='_compute_estado_cobranza', store=True,
        help='Cruza el estado de pago de la factura con la recepción del comprobante de '
             'retención. "Pagado sin comprobante" es el caso de riesgo real: el cliente ya '
             'pagó (Art. 13 — la retención se practica al pago/abono) pero el comprobante '
             'no ha llegado, ya sea porque no lo enviaron o se traspapeló de su lado.')

    # ── Documento ────────────────────────────────────────────────────────────
    tipo_documento = fields.Char(string='Tipo de Transacción')
    nro_documento = fields.Char(
        string='Nro. Documento',
        help='Campo heredado (OCR). Use Factura para el vínculo contable.',
    )
    nro_factura_match = fields.Char(
        string='N° Factura (match SENIAT)',
        compute='_compute_nro_factura_match',
        help='N° de factura que realmente se usa para el Nivel 2 de '
             'conciliación SENIAT (invoice_id.name si hay factura vinculada, '
             'si no nro_documento) -- ver _do_conciliar.',
    )
    nro_control = fields.Char(string='Nro. Control', tracking=True)
    nro_factura = fields.Char(
        string='N° Factura (Carga)', tracking=True,
        help='Copiado de la factura al crear la retención (ver '
             'account.move.nro_factura) -- junto con N° Control, evita la '
             'retención al 100% si al menos uno de los dos está presente. '
             'Distinto de "N° Factura" (invoice_name, related a '
             'invoice_id.name): ese es el nombre real de la factura en '
             'Odoo (puede haber caído al N° Control como respaldo si '
             'faltaba este dato al cargar); este es siempre el dato tal '
             'cual vino del Libro de Ventas, sin fallback -- etiqueta '
             'distinta a propósito (evita advertencia de labels duplicados '
             'en ir_model.py, ambos campos convivían con el mismo label).',
    )
    doc_afectado = fields.Char(string='Documento Afectado')
    discrepancia_doc_afectado_no_encontrado = fields.Boolean(
        string='Discrepancia: Doc. Afectado no encontrado', copy=False,
        help='El `doc_afectado` de la NC/ND que originó este registro no '
             'coincidió (ni normalizado, ver ve_conciliacion.py::_norm_ctrl/'
             '_norm_factura) con ninguna factura de la compañía al momento '
             'de confirmar la carga. Puede ser una factura de un período '
             'aún no cargado, o un error del archivo -- no bloquea, queda '
             'para revisión (PROPUESTA_NOTAS_CREDITO_DEBITO.md sección 3.7).')
    discrepancia_doc_afectado_detalle = fields.Char(
        string='Detalle discrepancia Doc. Afectado', copy=False)
    discrepancia_retencion_confirmada = fields.Boolean(
        string='Discrepancia: NC/ND sobre retención ya confirmada', copy=False,
        help='La NC/ND que originó este registro afecta una factura cuya '
             'retención ya estaba `confirmado`/`declarado` -- se generó un '
             'movimiento nuevo en el período de la NC/ND en vez de tocar el '
             'original (Caso B, ver PROPUESTA_NOTAS_CREDITO_DEBITO.md '
             'sección 1 y 3.7), queda marcado para revisión, no bloquea.')
    discrepancia_retencion_confirmada_detalle = fields.Char(
        string='Detalle discrepancia retención confirmada', copy=False)
    zona = fields.Char(
        string='Zona/Planta',
        help='Copiado de la factura al crear la retención (ver account.move.zona) '
             '— informativo, para seguimiento/filtro por zona. Un solo RIF/una sola '
             'Declaración IVA sigue cubriendo todas las zonas.',
    )

    # ── Anulación ────────────────────────────────────────────────────────────
    motivo_anulacion = fields.Text(string='Motivo de Anulación')
    anulado_por = fields.Many2one(
        'res.users',
        string='Anulado por',
        readonly=True,
    )

    # ── Asiento contable ─────────────────────────────────────────────────────
    asiento_id = fields.Many2one(
        'account.move',
        string='Asiento Contable',
        readonly=True,
        copy=False,
    )

    # ── Conciliación (legacy + nueva) ────────────────────────────────────────
    estado_conciliacion = fields.Selection([
        ('pendiente',          'Por Conciliar'),
        ('solo_odoo',          'Sin SENIAT'),
        ('solo_seniat',        'Solo en SENIAT'),
        ('diferencia',         'Diferencia de Monto'),
        ('conciliada_norec',   'No Recibido SENIAT OK'),
        ('listo_declarar',     'Listo para Declarar'),
        ('declarado',          'Declarado'),
        # legacy — no se asignan en código nuevo pero pueden existir en BD
        ('conciliada',         'Conciliada'),
        ('aprobado_declarar',  'Aprobado para Declarar'),
    ], string='Estado Conciliación', default='pendiente')

    monto_seniat = fields.Float(
        string='Monto según SENIAT',
        digits=(16, 2),
    )
    seniat_rif = fields.Char(
        string='RIF (SENIAT)', copy=False,
        help='RIF Agente tal cual lo trae SENIAT en la retención emparejada '
             '-- puede diferir en formato del RIF propio (guiones/espacios), '
             'ver ve_conciliacion.py::_do_conciliar (normalización).',
    )
    seniat_nro_control = fields.Char(
        string='N° Control (SENIAT)', copy=False,
        help='N° de Control tal cual lo trae SENIAT en la retención '
             'emparejada -- casi nunca coincide letra por letra con el '
             'propio (ceros de relleno, guiones), por eso se comparan '
             'ambos normalizados en vez de literal.',
    )
    seniat_nro_documento = fields.Char(
        string='N° Doc (SENIAT)', copy=False,
        help='N° de Documento/Factura tal cual lo trae SENIAT en la '
             'retención emparejada.',
    )
    fecha_conciliacion = fields.Datetime(string='Fecha Conciliación')
    # Auditoría del cruce SENIAT -- pedido explícito 2026-08-11, tras medir
    # con datos reales de Cementos (Enero 2026) que el N° de Control que el
    # cliente reporta al SENIAT casi nunca coincide LITERAL con el del
    # Libro de Ventas (ceros de relleno, guiones): normalizando ambos lados
    # (solo dígitos, sin ceros a la izquierda) se recupera 72% de las
    # retenciones que antes quedaban "Solo en SENIAT"; comparando también
    # por N° de Factura cuando no hay Control, 5.5% más. Ver
    # ve_conciliacion.py::_do_conciliar / _norm_ctrl / _norm_factura.
    nivel_match = fields.Selection([
        ('n1', 'N1 — RIF + N° Control'),
        ('n2', 'N2 — RIF + N° Factura'),
    ], string='Nivel de Match SENIAT', copy=False,
        help='Cómo se encontró la coincidencia con el SENIAT. N1 = mismo RIF '
             'y N° de Control (match fuerte). N2 = mismo RIF y N° de Factura, '
             'usado solo cuando la retención no tiene N° de Control.')
    matched_por_normalizacion = fields.Boolean(
        string='Match por Normalización', copy=False,
        help='El cruce con el SENIAT solo funcionó después de normalizar el '
             'N° de Control/Factura (quitar ceros a la izquierda, guiones, '
             'prefijos) — el texto crudo no coincidía literalmente. No es un '
             'error, pero conviene revisar antes de declarar.')
    conciliacion_id = fields.Many2one(
        've.conciliacion.periodo',
        string='Período Retención',
        ondelete='set null',
    )

    # ── Eje 3: Declaración — independiente de `state` (Eje 1) y de
    # `estado_conciliacion` (Eje 2). Rediseño de 3 ejes, 2026-07-23 (ver
    # especificaciones/REQUISITOS.md). ETAPA 3: campo escribible real — las
    # transiciones de ve_declaracion_iva.py lo escriben directo y ya NO tocan
    # `state` para declarar/deshacer declaración (antes: Etapas 1/2, era un
    # espejo computado de `state == 'declarado'`).
    estado_declaracion = fields.Selection([
        ('no_declarado', 'No Declarado'),
        ('declarado',    'Declarado'),
    ], string='Estado Declaración', default='no_declarado',
        copy=False, tracking=True, index=True)

    # ── Eje 1 (visual): Recepción, con la diferencia de monto del
    # comprobante ya incorporada como sub-etiqueta — reemplaza gradualmente
    # el uso de `state` a secas para mostrar. "c/Dif" no es una transición
    # propia, es Recibido/Confirmado + diferencia detectada.
    estado_recepcion = fields.Selection([
        ('esperado',       'No Recibido'),
        ('vencido',        'Vencido'),
        ('recibido',       'Recibido'),
        ('recibido_dif',   'Recibido · c/Dif'),
        ('confirmado',     'Confirmado'),
        ('confirmado_dif', 'Confirmado · c/Dif'),
        ('anulado',        'Anulado'),
    ], string='Estado Recepción', compute='_compute_estado_recepcion', store=True)

    # ── Recordatorios: 3 banderas booleanas que reemplazan las tablas de
    # ~30 strings combinados de `estado_visual` para decidir qué botón de
    # recordatorio aplica. Una sola fuente de verdad en vez de reglas
    # duplicadas en vistas + 2 dispatchers Python.
    necesita_envio_comp = fields.Boolean(
        string='Necesita pedir comprobante',
        compute='_compute_necesita_recordatorio', store=True,
        help='El comprobante físico no ha llegado (o llegó tarde/con diferencia '
             'después de declarado) — hay que pedirlo/aclararlo con el cliente.')
    necesita_aclarar_dif_seniat = fields.Boolean(
        string='Necesita aclarar diferencia SENIAT',
        compute='_compute_necesita_recordatorio', store=True,
        help='El SENIAT reporta un monto distinto al esperado, sin que el '
             'comprobante físico esté ya declarado con diferencia propia.')
    necesita_reportar_seniat = fields.Boolean(
        string='Necesita reportar a SENIAT',
        compute='_compute_necesita_recordatorio', store=True,
        help='El agente de retención todavía no reporta esta retención al SENIAT.')



    # ── Declaración ──────────────────────────────────────────────────────────
    incluir_declaracion = fields.Boolean(
        string='C.66',
        default=True,
        tracking=True,
        help='Si se desmarca, este comprobante NO se suma al Campo 66 de la declaración IVA.',
    )
    declarado_sin_comprobante = fields.Boolean(
        string='Declarado sin Comprobante',
        default=False,
        copy=False,
        tracking=True,
        help='Se marca automáticamente al presentar la Declaración IVA si esta '
             'retención nunca llegó a Confirmado (No Recibido/Vencido) pero '
             'estaba incluida en C.66 — el SENIAT puede o no confirmarla '
             '(ver Estado Conciliación), pero el comprobante físico nunca '
             'llegó al momento de declarar. Independiente de si el SENIAT la '
             'reporta o no.',
    )
    diferencia_monto = fields.Float(
        string='Diferencia',
        compute='_compute_diferencia_monto',
        store=True,
        digits=(16, 2),
    )
    fecha_ultimo_recordatorio = fields.Datetime(
        string='Último Recordatorio Enviado',
        tracking=True,
        help='Fecha/hora del último recordatorio por email enviado al contacto del cliente.',
    )
    fecha_ultima_llamada = fields.Datetime(
        string='Última Llamada',
        tracking=True,
        help='Fecha/hora del último registro de llamada telefónica al contacto del cliente.',
    )

    # ── Misc ─────────────────────────────────────────────────────────────────
    notas = fields.Text(string='Notas')
    is_locked = fields.Boolean(
        string='Bloqueado',
        compute='_compute_is_locked',
    )

    # ════════════════════════════════════════════════════════════════════════
    # COMPUTES
    # ════════════════════════════════════════════════════════════════════════

    # "¿Recibió esta retención un comprobante físico real?" — antes se
    # duplicaba esta misma pregunta (state in {...} + excepción
    # declarado_sin_comprobante) en 6 sitios distintos del módulo, cada uno
    # con su propia copia del set. Ahora `estado_recepcion` (Eje 1, ya la
    # resuelve una sola vez, incluyendo el caso "Declarado sin Comprobante")
    # y todo lo demás se apoya en ese único cómputo.
    _RECIBIDO_ESTADOS = ('recibido', 'recibido_dif', 'confirmado', 'confirmado_dif')

    @api.depends('state', 'estado_declaracion')
    def _compute_is_locked(self):
        for rec in self:
            rec.is_locked = rec.state in ('confirmado', 'anulado') or rec.estado_declaracion == 'declarado'

    @api.depends('estado_recepcion')
    def _compute_comprobante_recibido(self):
        for rec in self:
            rec.comprobante_recibido = rec.estado_recepcion in rec._RECIBIDO_ESTADOS

    @api.depends('payment_state', 'estado_recepcion')
    def _compute_estado_cobranza(self):
        pagado = {'paid', 'in_payment'}
        for rec in self:
            if rec.estado_recepcion == 'anulado' or rec.payment_state not in pagado:
                rec.estado_cobranza = 'no_pagado'
            elif rec.estado_recepcion in rec._RECIBIDO_ESTADOS:
                rec.estado_cobranza = 'pagado_con_comprobante'
            else:
                rec.estado_cobranza = 'pagado_sin_comprobante'

    @api.depends('monto_retenido', 'comp_monto_retenido', 'estado_recepcion')
    def _compute_monto_recibido(self):
        for rec in self:
            if rec.estado_recepcion not in rec._RECIBIDO_ESTADOS:
                rec.monto_recibido = 0.0
            elif rec.comp_monto_retenido:
                rec.monto_recibido = rec.comp_monto_retenido
            else:
                rec.monto_recibido = rec.monto_retenido

    @api.depends('monto_recibido', 'monto_retenido', 'incluir_declaracion', 'state')
    def _compute_monto_c66(self):
        for rec in self:
            if not rec.incluir_declaracion or rec.state == 'anulado':
                rec.monto_c66 = 0.0
            elif rec.state in ('esperado', 'vencido'):
                # No recibido con C.66 activo: se usa el monto esperado
                rec.monto_c66 = rec.monto_retenido
            else:
                # Recibido (borrador o confirmado)
                rec.monto_c66 = rec.monto_recibido if rec.monto_recibido > 0 else rec.monto_retenido

    @api.onchange('incluir_declaracion')
    def _onchange_incluir_declaracion(self):
        if not self.incluir_declaracion or self.state == 'anulado':
            self.monto_c66 = 0.0
        elif self.state in ('esperado', 'vencido'):
            self.monto_c66 = self.monto_retenido
        else:
            self.monto_c66 = self.monto_recibido if self.monto_recibido > 0 else self.monto_retenido

    @api.depends('conciliacion_id', 'conciliacion_id.periodo_retencion')
    def _compute_periodo_retencion(self):
        for rec in self:
            rec.periodo_retencion = rec.conciliacion_id.periodo_retencion or ''

    @api.depends('state', 'comp_monto_retenido', 'monto_retenido', 'declarado_sin_comprobante',
                 'fecha_vencimiento_entrega')
    def _compute_estado_recepcion(self):
        hoy = fields.Date.today()
        for rec in self:
            has_diff = (
                rec.comp_monto_retenido > 0
                and rec.monto_retenido > 0
                and abs(rec.comp_monto_retenido - rec.monto_retenido) > 0.01
            )
            s = rec.state
            llego_papel = rec.comp_monto_retenido > 0

            if s == 'anulado':
                rec.estado_recepcion = 'anulado'

            elif rec.declarado_sin_comprobante and not llego_papel:
                # Se declaró (C.66 a mano) sin haber recibido nunca el papel —
                # su recepción real sigue sin llegar, sin importar que `state`
                # (en registros legado de antes de la Etapa 4 de este
                # rediseño) todavía diga 'declarado'/'conciliado'.
                rec.estado_recepcion = (
                    'vencido'
                    if rec.fecha_vencimiento_entrega and rec.fecha_vencimiento_entrega <= hoy
                    else 'esperado'
                )

            elif s in ('esperado', 'vencido'):
                if llego_papel:
                    # El papel llegó tarde, después de declarar, sin pasar
                    # por Recibir/Confirmar (Etapa 3: declarar ya no mueve
                    # `state`, se queda en esperado/vencido) — ya no tiene
                    # sentido seguir diciendo "No Recibido"/"Vencido".
                    rec.estado_recepcion = 'recibido_dif' if has_diff else 'recibido'
                else:
                    rec.estado_recepcion = s

            elif s == 'borrador':
                rec.estado_recepcion = 'recibido_dif' if has_diff else 'recibido'

            else:
                # confirmado (y legado conciliado/declarado hasta la Etapa 4)
                rec.estado_recepcion = 'confirmado_dif' if has_diff else 'confirmado'

    @api.depends('estado_recepcion', 'estado_conciliacion', 'estado_declaracion')
    def _compute_necesita_recordatorio(self):
        _SENIAT_OK = frozenset({
            'conciliada_norec', 'listo_declarar', 'declarado',
            'conciliada', 'aprobado_declarar',
        })
        for rec in self:
            anulado = rec.estado_recepcion == 'anulado'
            sin_papel = rec.estado_recepcion in ('esperado', 'vencido')
            con_dif = rec.estado_recepcion in ('recibido_dif', 'confirmado_dif')
            declarado = rec.estado_declaracion == 'declarado'
            c = rec.estado_conciliacion

            # "Declarado c/Dif" (papel llegó con diferencia, o el SENIAT
            # reporta diferencia, después de declarado) tiene prioridad sobre
            # "Declarado Sin SENIAT" — mismo criterio que ya usaba
            # _compute_estado_visual para 'declarado_con_dif'.
            excluir_por_declarado = declarado and (sin_papel or con_dif or c == 'diferencia')

            rec.necesita_envio_comp = not anulado and (
                sin_papel or (declarado and (con_dif or c == 'diferencia')))
            rec.necesita_aclarar_dif_seniat = not anulado and not declarado and c == 'diferencia'
            rec.necesita_reportar_seniat = (
                not anulado and not excluir_por_declarado
                and c not in _SENIAT_OK and c != 'diferencia')

    @api.depends('monto_iva', 'monto_iva_red', 'porcentaje_retencion', 'state')
    def _compute_monto_retenido(self):
        for rec in self:
            if rec.state == 'anulado':
                rec.monto_retenido = 0.0
            else:
                total_iva = rec.monto_iva + rec.monto_iva_red
                rec.monto_retenido = round(
                    total_iva * rec.porcentaje_retencion / 100, 2)

    @api.depends('monto_retenido', 'monto_retenido_archivo', 'viene_de_libro_ventas')
    def _compute_diferencia_vs_archivo(self):
        for rec in self:
            if not rec.viene_de_libro_ventas:
                rec.diferencia_vs_archivo = 0.0
                rec.pct_diferencia_archivo = ''
                continue
            dif = round(rec.monto_retenido - rec.monto_retenido_archivo, 2)
            rec.diferencia_vs_archivo = dif
            # Sin base para calcular % si el archivo traía 0 (división por
            # cero, no 0% -- "Archivo=0" dice explícitamente por qué no hay
            # un porcentaje, en vez de mostrar 0% y sugerir falsamente que
            # no hay diferencia).
            if rec.monto_retenido_archivo:
                rec.pct_diferencia_archivo = f'{round(dif / rec.monto_retenido_archivo * 100, 1)}%'
            elif dif:
                rec.pct_diferencia_archivo = 'Archivo=0'
            else:
                rec.pct_diferencia_archivo = '0%'

    @api.depends('monto_iva', 'monto_iva_red', 'monto_base', 'monto_base_red')
    def _compute_totales_odoo(self):
        for rec in self:
            rec.monto_iva_total = round(rec.monto_iva + rec.monto_iva_red, 2)
            rec.base_imponible_total = round(rec.monto_base + rec.monto_base_red, 2)

    @api.depends('invoice_id', 'invoice_id.invoice_date',
                 'invoice_id.invoice_date_due', 'periodo')
    def _compute_fecha_vencimiento(self):
        cfg = self.env['ir.config_parameter'].sudo()
        feriados_extra = set()
        for iso in cfg.get_param('ve_retencion_iva.feriados_adicionales', '').split(','):
            iso = iso.strip()
            if iso:
                try:
                    feriados_extra.add(date.fromisoformat(iso))
                except ValueError:
                    pass

        for rec in self:
            # Ancla: fecha de vencimiento de la factura (invoice_date_due,
            # estimación del pago según términos de pago — Art. 13: la
            # retención ocurre al momento del pago/abono en cuenta, no de
            # la factura). Si no hay fecha de vencimiento, se usa la fecha
            # de factura como respaldo (venta de contado: factura ≈ pago).
            invoice_date = None
            if rec.invoice_id:
                invoice_date = rec.invoice_id.invoice_date_due or rec.invoice_id.invoice_date
            if not invoice_date and rec.periodo:
                try:
                    y, m = int(rec.periodo[:4]), int(rec.periodo[5:7])
                    last = calendar.monthrange(y, m)[1]
                    invoice_date = date(y, m, last)
                except (ValueError, IndexError):
                    pass
            rec.fecha_vencimiento_entrega = (
                _deadline_from_invoice_date(invoice_date, feriados_extra)
                if invoice_date else False
            )

    @api.depends('monto_retenido', 'monto_seniat')
    def _compute_diferencia_monto(self):
        for rec in self:
            rec.diferencia_monto = rec.monto_retenido - rec.monto_seniat

    @api.depends('invoice_id', 'invoice_id.name', 'nro_documento')
    def _compute_nro_factura_match(self):
        # Mismo criterio que usa _do_conciliar para Nivel 2 (RIF+Factura):
        # si hay factura vinculada, su número manda sobre nro_documento
        # (campo heredado de OCR, casi siempre vacío cuando ya hay
        # invoice_id). Solo para mostrar en "Ver Normalizadas" el dato
        # real que se usó para machear -- ver ve_conciliacion.py::
        # _do_conciliar, wh_factura_raw.
        for rec in self:
            rec.nro_factura_match = (
                rec.invoice_id.name if rec.invoice_id else (rec.nro_documento or False))

    @api.depends('fecha', 'fecha_vencimiento_entrega', 'state')
    def _compute_fuera_plazo(self):
        today = date.today()
        for rec in self:
            if rec.fecha and rec.fecha_vencimiento_entrega:
                # Recibido: ¿llegó tarde?
                rec.fuera_plazo = rec.fecha > rec.fecha_vencimiento_entrega
            elif rec.fecha_vencimiento_entrega and rec.state in ('esperado', 'vencido'):
                # No recibido aún: ¿ya venció el plazo?
                rec.fuera_plazo = today > rec.fecha_vencimiento_entrega
            else:
                rec.fuera_plazo = False

    # ════════════════════════════════════════════════════════════════════════
    # VALIDACIONES
    # ════════════════════════════════════════════════════════════════════════

    @api.constrains('state', 'nro_control', 'nro_factura', 'porcentaje_retencion')
    def _check_confirmado_nro_control(self):
        for rec in self:
            if (rec.state == 'confirmado'
                    and not rec.nro_control
                    and not rec.nro_factura
                    and (rec.porcentaje_retencion or 0) < 100.0):
                raise ValidationError(
                    f'El N° Control o el N° Factura es obligatorio para '
                    f'confirmar la retención de {rec.partner_id.name or "—"} '
                    f'(excepción: retenciones al 100%).'
                )

    @api.constrains('invoice_id')
    def _check_invoice_id_requerido(self):
        """La retención SIEMPRE se crea a partir de una factura — nunca debe
        poder quedar sin invoice_id (se encontró un caso real de dato huérfano
        en el generador de histórico de demo, corregido por separado).
        Se valida vía constraint en vez de required=True en el campo para no
        arriesgar una migración fallida si ya existieran filas huérfanas
        antiguas en la base — esto solo bloquea que se creen nuevas."""
        for rec in self:
            if not rec.invoice_id:
                raise ValidationError(
                    f'La retención de {rec.partner_id.name or "—"} debe estar '
                    'vinculada a una factura (invoice_id) — no puede quedar sin '
                    'factura asociada.'
                )

    @api.onchange('invoice_id')
    def _onchange_invoice_id_nro_control(self):
        """Auto-completa N° Control / N° Factura desde la factura cuando el campo está vacío."""
        if self.invoice_id:
            if self.invoice_id.nro_control and not self.nro_control:
                self.nro_control = self.invoice_id.nro_control
            if self.invoice_id.nro_factura and not self.nro_factura:
                self.nro_factura = self.invoice_id.nro_factura

    @api.onchange('name')
    def _onchange_name_warn(self):
        """Advierte si el N° comprobante no tiene 14 dígitos (no bloquea)."""
        if self.name and not _NRO_COMP_RE.match(self.name):
            return {
                'warning': {
                    'title': 'N° Comprobante',
                    'message': (
                        'El N° Comprobante debería tener exactamente '
                        '14 dígitos numéricos (aaaaMM + 8 consecutivo).'
                    ),
                }
            }

    def _validar_para_confirmar(self):
        self.ensure_one()
        errors = []
        if not self.invoice_id:
            errors.append('La retención debe tener una factura vinculada.')
        if not self.name:
            errors.append('El N° Comprobante es obligatorio.')
        if (not self.nro_control and not self.nro_factura
                and (self.porcentaje_retencion or 0) < 100.0):
            errors.append(
                'El N° Control o el N° Factura es obligatorio para confirmar '
                '(solo se omite en retenciones al 100%).'
            )
        if not self.partner_id:
            errors.append('El Cliente / Agente de Retención es obligatorio.')
        if self.partner_id and self.partner_id.vat:
            if not _RIF_RE.match(self.partner_id.vat):
                errors.append(
                    f'El RIF del cliente ({self.partner_id.vat}) no tiene el '
                    f'formato correcto (ej: J-12345678-9).'
                )
        if not self.monto_retenido:
            errors.append('El Monto Retenido debe ser mayor a cero.')
        elif self.monto_retenido < 0 and self.tipo_documento != '03':
            # Un monto negativo solo es válido para el ajuste automático
            # que genera una Nota de Crédito sobre una retención ya
            # confirmada/declarada (tipo_documento='03', ver
            # _crear_ajuste_nc_negativo en ve_conecta_carga_ventas.py) --
            # 2026-09-03. Cualquier otro comprobante con monto negativo
            # sigue siendo un error real de dato.
            errors.append(
                'El Monto Retenido debe ser mayor a cero (un monto negativo '
                'solo es válido para el ajuste automático de una Nota de '
                'Crédito, Tipo de Transacción 03).'
            )
        if errors:
            raise UserError('\n'.join(errors))

    # ════════════════════════════════════════════════════════════════════════
    # ORM OVERRIDES
    # ════════════════════════════════════════════════════════════════════════

    @api.model_create_multi
    def create(self, vals_list):
        company_id = self.env.company.id
        seq = self.env['ir.sequence'].next_by_code
        for vals in vals_list:
            vals.setdefault('estado_conciliacion', 'pendiente')
            vals.setdefault('company_id', company_id)
            if not vals.get('ref'):
                vals['ref'] = seq('ve.wh.iva') or f'RET-IVA-C/{fields.Date.today().year}/????'
        records = super().create(vals_list)
        # Vincular al período de conciliación abierto más reciente DE LA
        # MISMA COMPAÑÍA — sin el filtro, una retención de otra compañía
        # (ej. sandbox de piloto) se enganchaba al período abierto de
        # cualquier otra compañía (MULTI-02).
        #
        # Bug real encontrado 2026-07-30: este auto-vínculo corre en CADA
        # creación (vía el hook nativo _ve_crear_retencion_esperada, que
        # dispara al postear CUALQUIER factura, incluidas las que crea
        # CONECTA-14 al cargar un Libro de Ventas histórico) — "el período
        # abierto más reciente" no tiene relación con la fecha real de la
        # factura. Con una carga en dos lotes de años distintos, el segundo
        # lote quedaba enganchado aquí mismo, en el instante de creación, al
        # período que el primer lote acababa de crear — antes de que el
        # propio bucle de CONECTA-14 (ve_conecta_carga_ventas.py::
        # action_confirmar, que sí vincula por la fecha real de cada
        # factura vía _asegurar_periodo) llegara a mirar esa retención: ya
        # no aparecía "huérfana" (conciliacion_id vacío), así que nunca se
        # corregía. CONECTA-14 ahora crea sus facturas con el contexto
        # `ve_periodo_asignacion_manual` para desactivar este auto-vínculo
        # y dejar la retención huérfana hasta que su propio bucle la
        # vincule por fecha.
        if not self.env.context.get('ve_periodo_asignacion_manual'):
            # Fix real 2026-08-14 (Cementos): 10.226 retenciones creadas
            # durante un reproceso masivo de cargas quedaron enganchadas
            # al período con el ID MÁS ALTO (fallback viejo, "abierto más
            # reciente") pese a que varios de los llamadores SÍ pasaban
            # ve_periodo_asignacion_manual en algún punto de la cadena --
            # no se pudo aislar con certeza en qué paso exacto se perdía
            # el contexto (`self.with_context(...)` reasigna la variable
            # local, pero un recordset ya calculado con el `self` viejo,
            # como `lineas_a_procesar`, conserva su propio env/contexto
            # aunque el caller siga usando la variable reasignada después
            # -- sospecha principal, sin confirmar al 100%). En vez de
            # perseguir cada camino posible, el create() ahora es correcto
            # por defecto: si la retención ya trae invoice_id con fecha
            # real, se vincula por ESA fecha (mismo criterio que
            # _asegurar_periodo, ya usado por el resto del módulo) antes
            # de mirar el contexto en absoluto. El "período abierto más
            # reciente" queda como último recurso, solo para retenciones
            # sin factura vinculada o sin fecha (no debería ser el caso
            # normal en este módulo).
            Periodo = self.env['ve.conciliacion.periodo']
            for rec in records.filtered(lambda r: not r.conciliacion_id):
                fecha_ref = rec.invoice_id.invoice_date if rec.invoice_id else False
                if fecha_ref:
                    rec.conciliacion_id = Periodo._asegurar_periodo(
                        rec.company_id, fecha_ref).id
            sin_periodo = records.filtered(lambda r: not r.conciliacion_id)
            if sin_periodo:
                open_period = self.env['ve.conciliacion.periodo'].search(
                    [
                        ('estado', 'not in', ['aprobado', 'declarado']),
                        ('company_id', '=', company_id),
                    ],
                    order='id desc', limit=1,
                )
                if open_period:
                    sin_periodo.write({'conciliacion_id': open_period.id})
        return records

    # Campos que siguen editables sobre una retención ya declarada
    # (estado_declaracion == 'declarado') — MEJORA-INMUTABILIDAD-01. Son
    # exactamente los que `wizard_subir_comprobante.action_guardar` escribe
    # cuando el papel físico llega tarde (FIX-COMP-DECLARADO-01): dejan
    # constancia documental sin tocar la declaración ya presentada.
    _CAMPOS_EDITABLES_DECLARADO = frozenset({
        'name', 'tipo_documento', 'nro_documento', 'nro_control',
        'comp_base_16', 'comp_iva_16', 'comp_base_8', 'comp_iva_8',
        'comp_base_exento', 'comp_base_nogravado', 'comp_monto_retenido',
        'canal_recepcion', 'fecha', 'state',
        # El propio Eje 3 y sus derivados — se marcan "declarado"/
        # "declarado_sin_comprobante" al declarar.
        'estado_declaracion', 'declarado_sin_comprobante',
        # El seguimiento (recordatorio/llamada) sigue activo sobre una
        # retención "declarado_sin_comprobante" — el papel físico aún no
        # llegó aunque ya se declaró al SENIAT (ver FIX-COMP-DECLARADO-01).
        'fecha_ultimo_recordatorio', 'fecha_ultima_llamada',
    })

    def write(self, vals):
        campos_bloqueados = set(vals) - self._CAMPOS_EDITABLES_DECLARADO
        if campos_bloqueados:
            ya_declaradas = self.filtered(lambda r: r.estado_declaracion == 'declarado')
            if ya_declaradas:
                raise UserError(
                    'No se puede modificar "%s" — la retención %s ya fue '
                    'declarada al SENIAT (Forma 030) y no se puede revertir.'
                    % (', '.join(sorted(campos_bloqueados)),
                       ya_declaradas[0].ref or ya_declaradas[0].name or ya_declaradas[0].id)
                )
        return super().write(vals)

    # ════════════════════════════════════════════════════════════════════════
    # TRANSICIONES DE ESTADO
    # ════════════════════════════════════════════════════════════════════════

    def action_recibir(self):
        """Esperado/Vencido → Recibido. Valida cliente y canal antes de transicionar."""
        for rec in self:
            if rec.state not in ('esperado', 'vencido'):
                raise UserError(
                    'Solo se puede marcar como recibido un comprobante '
                    'en estado No Recibido o Vencido.'
                )
            errors = []
            if not rec.partner_id:
                errors.append('El Cliente / Agente de Retención es obligatorio.')
            if not rec.canal_recepcion:
                errors.append(
                    'El Canal de Recepción es obligatorio.\n'
                    'Indique cómo llegó el comprobante (Email, WhatsApp, etc.).'
                )
            if not rec.name:
                errors.append('El N° Comprobante es obligatorio (14 dígitos).')
            if not rec.invoice_id and not rec.nro_documento and not rec.nro_control:
                errors.append(
                    'Debe indicar al menos el N° Factura o el N° Control '
                    'del comprobante recibido.'
                )
            if rec.monto_iva > 0 and not rec.comp_iva_16:
                errors.append(
                    'El monto IVA 16% del comprobante es obligatorio '
                    '(sección "Montos según Comprobante" → IVA 16%).'
                )
            if rec.monto_iva_red > 0 and not rec.comp_iva_8:
                errors.append(
                    'El monto IVA 8% del comprobante es obligatorio '
                    '(sección "Montos según Comprobante" → IVA 8%).'
                )
            if errors:
                raise UserError('\n'.join(errors))
            fecha_recepcion = rec.fecha or fields.Date.today()
            rec.write({
                'state': 'borrador',
                'fecha': fecha_recepcion,
            })
            # Si "Conciliar SENIAT" ya había encontrado el match antes de
            # que llegara el papel físico, había quedado en 'conciliada_norec'
            # ("No Recibido SENIAT OK") -- ese fork solo se evalúa dentro de
            # _do_conciliar y no se re-ejecuta solo porque el estado cambió,
            # así que sin este ajuste la etiqueta se queda pisada e
            # inconsistente (dice "No Recibido" con el comprobante ya
            # recibido). El monto no cambió, solo la recepción física, así
            # que el resultado es directo: pasa a 'listo_declarar' (mismo
            # destino que _do_conciliar le habría dado si el comprobante ya
            # hubiera estado recibido cuando corrió la conciliación).
            if rec.estado_conciliacion == 'conciliada_norec':
                rec.estado_conciliacion = 'listo_declarar'
                rec.message_post(
                    body='Estado Conciliación actualizado a "Listo para Declarar": '
                         'el comprobante ya fue recibido y el monto seguía '
                         'coincidiendo con el SENIAT.',
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            # Comprobante recibido fuera de plazo → reasignar al período activo
            # actual DE LA MISMA COMPAÑÍA (MULTI-02)
            if rec.fuera_plazo:
                periodo_activo = self.env['ve.conciliacion.periodo'].search(
                    [
                        ('estado', 'not in', ['aprobado', 'declarado']),
                        ('company_id', '=', rec.company_id.id),
                    ],
                    order='id desc', limit=1,
                )
                if periodo_activo and rec.conciliacion_id != periodo_activo:
                    periodo_orig = rec.conciliacion_id.periodo_retencion or '—'
                    rec.conciliacion_id = periodo_activo.id
                    rec.message_post(
                        body=Markup(
                            '<b>Reasignado al período activo</b> '
                            '<b>{nuevo}</b> por recepción fuera de plazo.<br/>'
                            'Período original: {orig}'
                        ).format(nuevo=periodo_activo.periodo_retencion, orig=periodo_orig),
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
            rec.message_post(
                body='Comprobante marcado como recibido.',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    def action_confirmar(self):
        """Borrador → Confirmado (crea asiento contable)."""
        for rec in self:
            if rec.state != 'borrador':
                raise UserError(
                    'Solo se puede confirmar un comprobante en estado Borrador.'
                )
            rec._validar_para_confirmar()
            asiento = rec._crear_asiento_contable()
            # C.66 (incluir_declaracion) se activa aquí, no en action_recibir
            # — tener el comprobante físico RECIBIDO no basta para declararlo
            # al SENIAT; CONFIRMARLO (validado internamente, con asiento
            # contable) es lo que la usuaria considera suficiente.
            rec.write({
                'state': 'confirmado',
                'asiento_id': asiento.id,
                'incluir_declaracion': True,
            })
            rec.message_post(
                body=f'Comprobante confirmado. Asiento contable: {asiento.name}',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    def action_aceptar_monto_archivo(self):
        """Ajusta % Retención para que el Monto Esperado (Odoo) coincida con
        Monto Retenido (Archivo) -- pedido explícito 2026-08-06: mecanismo
        para que el cliente acepte la diferencia y sus números cuadren con
        los propios (el archivo del Libro de Ventas), en vez de quedarse
        con lo que Odoo calculó por regla legal (ej. 100% sin N° Control).

        No toca monto_retenido directo (es un campo compute, no editable) --
        recalcula el % que hace falta para llegar al mismo monto, y deja el
        cambio auditado: % Retención ya tiene tracking=True (aparece solo en
        el chatter con quién/cuándo/de qué a qué), y además se postea una
        nota explícita aclarando que fue una aceptación manual del archivo,
        no un ajuste cualquiera de %."""
        for rec in self:
            if not rec.viene_de_libro_ventas or not rec.diferencia_vs_archivo:
                raise UserError(
                    'Esta retención no tiene diferencia contra el Libro de Ventas.'
                )
            if rec.is_locked:
                raise UserError(
                    'Esta retención está bloqueada (Confirmada/Anulada o el '
                    'período ya fue Declarado) -- no se puede ajustar el %.'
                )
            total_iva = rec.monto_iva + rec.monto_iva_red
            if not total_iva:
                raise UserError(
                    'No se puede calcular el % de retención: esta factura no '
                    'tiene IVA (base imponible en 0).'
                )
            pct_anterior = rec.porcentaje_retencion
            monto_anterior = rec.monto_retenido
            base_odoo = rec.base_imponible_total
            iva_odoo = rec.monto_iva_total
            pct_nuevo = round(rec.monto_retenido_archivo / total_iva * 100, 2)
            rec.write({
                'porcentaje_retencion': pct_nuevo,
                'diferencia_archivo_aceptada': True,
            })
            # Auditoría completa -- pedido explícito 2026-08-06: lo único
            # que este botón cambia de verdad es % Retención (y por
            # consiguiente Monto Retenido, que depende de él) -- Base
            # Imponible/Monto IVA no cambian (vienen de la factura ya
            # creada), se dejan acá solo como referencia de contexto para
            # que quede claro sobre qué base se calculó el % nuevo.
            rec.message_post(
                body=Markup(
                    f'<b>Monto del archivo aceptado</b> — {self.env.user.name} aceptó '
                    f'que el Monto Retenido del Libro de Ventas reemplace el calculado '
                    f'por Odoo para esta retención:<br/>'
                    f'&#8226; % Retención: {pct_anterior:.2f}% → {pct_nuevo:.2f}%<br/>'
                    f'&#8226; Monto Retenido: {monto_anterior:,.2f} Bs. → '
                    f'{rec.monto_retenido_archivo:,.2f} Bs.<br/>'
                    f'&#8226; Contexto (sin cambios) — Base Imponible: {base_odoo:,.2f} Bs. '
                    f'&#8226; Monto IVA: {iva_odoo:,.2f} Bs. (Archivo: '
                    f'{rec.monto_iva_archivo:,.2f} Bs.)'
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    def action_aceptar_monto_archivo_multi(self):
        """Variante en lote de action_aceptar_monto_archivo -- pedido
        explícito 2026-08-06 para usar desde selección múltiple en
        "Facturas con Diferencia". Filtra en silencio las que no califican
        (sin diferencia, bloqueadas, sin IVA) en vez de abortar todo el
        lote por una sola que no aplique -- mismo criterio que
        action_extraer_seniat_multi (omite períodos ya declarados sin
        interrumpir el resto)."""
        candidatas = self.filtered(
            lambda r: r.viene_de_libro_ventas and r.diferencia_vs_archivo
            and not r.is_locked and (r.monto_iva + r.monto_iva_red))
        if not candidatas:
            raise UserError(
                'Ninguna de las retenciones seleccionadas tiene una '
                'diferencia pendiente contra el Libro de Ventas (o están '
                'bloqueadas).'
            )
        candidatas.action_aceptar_monto_archivo()
        omitidas = len(self) - len(candidatas)
        mensaje = f'Se aceptó el monto del archivo en {len(candidatas)} retención(es).'
        if omitidas:
            mensaje += (f' {omitidas} de las seleccionadas se omitieron '
                        f'(sin diferencia real o bloqueadas).')
        # Notificación de éxito real, no un UserError -- levantar UserError
        # acá para mostrar el resumen (como se hizo al principio) usa el
        # mismo diálogo genérico de error de Odoo ("Operación no válida"),
        # aunque la acción haya funcionado bien -- confundía, reportado por
        # la usuaria 2026-08-06.
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Montos aceptados',
                'message': mensaje,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_anular(self):
        """Confirma la anulación (requiere motivo_anulacion previo).

        'vencido' agregado 2026-08-14: es solo 'esperado' que pasó la fecha
        límite (ver cron que hace a_vencer.write({'state': 'vencido'}) más
        abajo) -- en todo el resto del módulo se tratan como equivalentes
        (búsquedas de pendientes, KPIs, etc. siempre agrupan ('esperado',
        'vencido') juntos). No había forma de anular un comprobante vencido
        sin este fix -- hueco real encontrado al corregir en Cementos
        retenciones que nunca debieron generarse (partners que no son
        Contribuyente Especial) y que, por estar atrasadas, ya habían
        pasado a 'vencido'."""
        for rec in self:
            if rec.state not in ('esperado', 'vencido', 'borrador', 'confirmado'):
                raise UserError(
                    'Solo se puede anular un comprobante en estado '
                    'Esperado, Vencido, Borrador o Confirmado.'
                )
            if rec.conciliacion_id and rec.conciliacion_id.estado == 'declarado':
                raise UserError(
                    'No se puede anular un comprobante de un período '
                    'ya declarado ante el SENIAT.'
                )
            if not rec.motivo_anulacion:
                raise UserError(
                    'Indique el motivo de anulación antes de continuar.'
                )
            # Reversar asiento si existe
            if rec.asiento_id and rec.asiento_id.state == 'posted':
                rec.asiento_id.button_cancel()
            rec.write({
                'state': 'anulado',
                'anulado_por': self.env.user.id,
            })
            rec.message_post(
                body=(
                    f'Comprobante anulado por {self.env.user.name}.\n'
                    f'Motivo: {rec.motivo_anulacion}'
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    # ════════════════════════════════════════════════════════════════════════
    # LÓGICA CONTABLE
    # ════════════════════════════════════════════════════════════════════════

    def _crear_asiento_contable(self):
        """Genera el asiento de IVA Retenido por Cobrar / IVA por Pagar."""
        self.ensure_one()
        company = self.company_id or self.env.company

        # Por compañía (res.company), no ir.config_parameter global — cada
        # compañía (ej. cada cliente de un despacho contable) tiene su propio
        # plan de cuentas y no puede compartir account_id con otra.
        cta_cobrar = company.ve_cuenta_iva_retenido_cobrar_id
        cta_pagar  = company.ve_cuenta_iva_por_pagar_id

        if not cta_cobrar or not cta_pagar:
            raise UserError(
                f'Configure las cuentas contables de "{company.name}" en\n'
                'Ajustes → Contabilidad → Retenciones IVA Venezuela:\n'
                '  • IVA Retenido por Cobrar\n'
                '  • IVA por Pagar'
            )

        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', company.id)],
            limit=1,
        )
        if not journal:
            raise UserError(
                'No se encontró un diario Miscelánea (tipo General) '
                'para la empresa. Cree uno en Contabilidad → Configuración → Diarios.'
            )

        partner_id = self.partner_id.id if self.partner_id else False
        ref = self.name or f'RET-IVA-{self.id}'

        # Monto negativo (ajuste automático de Nota de Crédito sobre una
        # retención ya confirmada/declarada, tipo_documento='03', ver
        # _crear_ajuste_nc_negativo) -- invertir débito/crédito en vez de
        # pasar un débito negativo, que no es partida doble válida.
        # 2026-09-03.
        monto_abs = abs(self.monto_retenido)
        es_reversa = self.monto_retenido < 0
        line_cobrar = {'debit': 0.0, 'credit': monto_abs} if es_reversa \
            else {'debit': monto_abs, 'credit': 0.0}
        line_pagar = {'debit': monto_abs, 'credit': 0.0} if es_reversa \
            else {'debit': 0.0, 'credit': monto_abs}

        asiento = self.env['account.move'].create({
            'move_type': 'entry',
            'ref': ref,
            'date': self.fecha or fields.Date.today(),
            'journal_id': journal.id,
            'company_id': company.id,
            'line_ids': [
                (0, 0, {
                    'account_id': cta_cobrar.id,
                    'name': f'IVA Retenido por Cobrar – {ref}',
                    'partner_id': partner_id,
                    **line_cobrar,
                }),
                (0, 0, {
                    'account_id': cta_pagar.id,
                    'name': f'IVA por Pagar – {ref}',
                    'partner_id': partner_id,
                    **line_pagar,
                }),
            ],
        })
        asiento.action_post()
        return asiento

    # ════════════════════════════════════════════════════════════════════════
    # ACCIONES HEREDADAS
    # ════════════════════════════════════════════════════════════════════════

    def action_imprimir_comprobante(self):
        return self.env.ref(
            've_retencion_iva.action_report_comprobante_retencion'
        ).report_action(self)

    def action_subir_comprobante(self):
        self.ensure_one()
        return {
            'name': 'Subir y Escanear Comprobante',
            'type': 'ir.actions.act_window',
            'res_model': 've.wh.iva.wizard.comprobante',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_wh_iva_id': self.id},
        }

    def action_abrir_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_toggle_c66(self):
        self.ensure_one()
        if self.estado_declaracion != 'declarado':
            self.incluir_declaracion = not self.incluir_declaracion

    def _generar_recordatorio_asunto_cuerpo(self, tipo):
        """Genera (asunto, label, cuerpo_texto_plano) para un tipo de recordatorio.

        El cuerpo es texto plano con saltos de línea — pensado para mostrarse
        editable en el wizard de confirmación antes de enviar (el usuario puede
        modificarlo). Se convierte a HTML recién al momento de enviar.

        tipo: 'envio_comp' | 'dif_seniat' | 'rep_seniat'
        """
        self.ensure_one()
        factura = self.invoice_id.name if self.invoice_id else (self.nro_documento or '—')

        if tipo == 'dif_seniat':
            asunto = f'Diferencia de monto vs SENIAT — {self.partner_id.name or ""}'
            label  = 'Recordatorio: Diferencia SENIAT'
            extra  = f'\n- Monto según SENIAT: {self.monto_seniat:,.2f} Bs'
        elif tipo == 'rep_seniat':
            asunto = f'Pendiente reporte a SENIAT — {self.partner_id.name or ""}'
            label  = 'Recordatorio: Reporte a SENIAT'
            extra  = ''
        else:  # envio_comp
            asunto = f'Pendiente envío de comprobante de retención IVA — {self.partner_id.name or ""}'
            label  = 'Recordatorio: Envío de Comprobante'
            extra  = ''

        cuerpo = (
            f'Estimado(a) {self.partner_id.name or "cliente"},\n\n'
            f'Le recordamos que tiene pendiente: {asunto}.\n\n'
            f'- Factura: {factura}\n'
            f'- N° Control: {self.nro_control or "—"}\n'
            f'- Período: {self.periodo or "—"}\n'
            f'- Monto: {self.monto_retenido:,.2f} Bs'
            f'{extra}\n\n'
            'Por favor gestione lo pendiente a la brevedad posible.'
        )
        return asunto, label, cuerpo

    def _enviar_recordatorio_tipo(self, tipo, cuerpo_override=None, email_override=None,
                                   contacto_nombre=None):
        """Base para los tres tipos de recordatorio de conciliación.

        Envía un email real al contacto del cliente (en vez de crear una
        actividad interna pendiente) y registra en la bitácora del
        comprobante y del período la auditoría de que se envió: a quién,
        a qué correo, cuándo y quién lo envió.

        tipo: 'envio_comp' | 'dif_seniat' | 'rep_seniat'
        cuerpo_override: si se provee (texto plano, editado por el usuario en
            el wizard), reemplaza el mensaje generado automáticamente.
        email_override/contacto_nombre: pasados desde
            wizard_enviar_recordatorio.py cuando el usuario resolvió una
            persona de contacto específica (2026-08-01) — si no se proveen,
            se usa el email de la propia empresa (partner_id) como antes.
        """
        self.ensure_one()
        factura = self.invoice_id.name if self.invoice_id else (self.nro_documento or '—')
        asunto, label, cuerpo_generado = self._generar_recordatorio_asunto_cuerpo(tipo)
        cuerpo_texto = cuerpo_override if cuerpo_override is not None else cuerpo_generado
        cuerpo_cliente = Markup('<br/>').join(escape(linea) for linea in cuerpo_texto.split('\n'))

        email = email_override if email_override is not None else self.partner_id.email
        destinatario = (
            f'{contacto_nombre} ({self.partner_id.name})' if contacto_nombre
            else (self.partner_id.name or 'cliente')
        )
        ahora = fields.Datetime.now()
        usuario = self.env.user.name
        self.fecha_ultimo_recordatorio = ahora
        if email:
            self.message_post(
                body=cuerpo_cliente,
                subject=label,
                partner_ids=[self.partner_id.id],
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            auditoria = Markup(
                '<b>{label}</b> enviado por email a <b>{partner}</b> '
                '&lt;{email}&gt; el {fecha} por <b>{usuario}</b>.'
            ).format(
                label=label, partner=escape(destinatario),
                email=escape(email), fecha=ahora.strftime('%d/%m/%Y %H:%M'),
                usuario=escape(usuario),
            )
            notif_mensaje = f'Recordatorio enviado por email a {destinatario} <{email}>.'
            notif_tipo = 'success'
        else:
            auditoria = Markup(
                '⚠ <b>{label}</b>: no se pudo enviar por email — '
                '<b>{partner}</b> no tiene correo configurado. '
                'Registrado únicamente en la bitácora interna, {fecha}, por <b>{usuario}</b>.'
            ).format(
                label=label, partner=escape(destinatario),
                fecha=ahora.strftime('%d/%m/%Y %H:%M'), usuario=escape(usuario),
            )
            notif_mensaje = (
                f'{destinatario} no tiene correo configurado — '
                'el recordatorio quedó registrado solo en la bitácora interna.'
            )
            notif_tipo = 'warning'
        self.message_post(
            body=auditoria,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        if self.conciliacion_id:
            comp_link = self._get_html_link()
            self.conciliacion_id.message_post(
                body=Markup(
                    '<b>{label}</b> → <b>{partner}</b> | '
                    '{link} | Factura: {factura} | '
                    'N° Control: {ctrl} | Monto: {monto} Bs{extra}'
                ).format(
                    label=label,
                    partner=escape(self.partner_id.name or 'cliente'),
                    link=comp_link,
                    factura=escape(factura),
                    ctrl=escape(self.nro_control or '—'),
                    monto=f'{self.monto_retenido:,.2f}',
                    extra=Markup(f'  |  SENIAT: {self.monto_seniat:,.2f} Bs') if tipo == 'dif_seniat' else '',
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': label,
                'message': notif_mensaje,
                'type': notif_tipo,
                'sticky': False,
            },
        }

    def action_abrir_registrar_llamada(self):
        """Abre un pop-up con los datos de contacto del cliente para llamar;
        al confirmar registra la llamada en el chatter del comprobante.
        ve_desde_lista_trabajo (ver ve_dashboard_iva_views.xml, context= en
        el <field name="lista_trabajo_ids">) viaja al wizard para que sepa
        si, al cerrar, debe reabrir el Dashboard (recomputa lista_trabajo_ids,
        que es un campo no almacenado) en vez de solo cerrarse."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Registrar Llamada',
            'res_model': 've.registrar.llamada.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_wh_iva_id': self.id,
                've_desde_lista_trabajo': self.env.context.get('ve_desde_lista_trabajo', False),
            },
        }

    def _abrir_wizard_recordatorio(self, tipo):
        """Pop-up de confirmación antes de enviar (mismo patrón que Llamada)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enviar Recordatorio',
            'res_model': 've.enviar.recordatorio.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_wh_iva_id': self.id,
                'default_tipo': tipo,
                've_desde_lista_trabajo': self.env.context.get('ve_desde_lista_trabajo', False),
            },
        }

    def action_enviar_recordatorio(self):
        self.ensure_one()
        if self.necesita_aclarar_dif_seniat:
            return self._abrir_wizard_recordatorio('dif_seniat')
        if self.necesita_reportar_seniat:
            return self._abrir_wizard_recordatorio('rep_seniat')
        return self._abrir_wizard_recordatorio('envio_comp')

    def action_recordatorio_envio_comp(self):
        return self._abrir_wizard_recordatorio('envio_comp')

    def action_recordatorio_dif_seniat(self):
        return self._abrir_wizard_recordatorio('dif_seniat')

    def action_recordatorio_rep_seniat(self):
        return self._abrir_wizard_recordatorio('rep_seniat')

    def _notif_sin_pendientes(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sin pendientes',
                'message': 'Ningún registro seleccionado requiere este recordatorio.',
                'type': 'info',
                'sticky': False,
            },
        }

    def _notif_recordatorios_enviados(self, cantidad):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Recordatorios enviados',
                'message': f'{cantidad} recordatorio(s) enviado(s).',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_enviar_recordatorios_normal_seleccion(self):
        """Envío masivo del recordatorio 'solicitar comprobante' (normal,
        no SENIAT) — a diferencia de mezclar los 3 tipos en un solo botón,
        esto deja elegir a propósito cuál mandar cuando se seleccionan
        varias filas. Pedido explícito 2026-07-30."""
        pendientes = self.filtered('necesita_envio_comp')
        if not pendientes:
            return self._notif_sin_pendientes()
        for wh in pendientes:
            wh._enviar_recordatorio_tipo('envio_comp')
        return self._notif_recordatorios_enviados(len(pendientes))

    def action_enviar_recordatorios_seniat_seleccion(self):
        """Envío masivo del recordatorio SENIAT (aclarar diferencia de
        monto, o reportar si no hay match) — elige el sub-tipo por
        registro según cuál bandera tenga cada uno, pero solo entre las
        2 variantes SENIAT (nunca 'solicitar comprobante')."""
        pendientes = self.filtered(
            lambda r: r.necesita_aclarar_dif_seniat or r.necesita_reportar_seniat)
        if not pendientes:
            return self._notif_sin_pendientes()
        for wh in pendientes:
            tipo = 'dif_seniat' if wh.necesita_aclarar_dif_seniat else 'rep_seniat'
            wh._enviar_recordatorio_tipo(tipo)
        return self._notif_recordatorios_enviados(len(pendientes))

    # ════════════════════════════════════════════════════════════════════════
    # CRON — RECORDATORIOS AUTOMÁTICOS DE VENCIMIENTO
    # ════════════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════════════
    # MÉTODOS PARA ASISTENTE IA
    # ════════════════════════════════════════════════════════════════════════

    @api.model
    def get_retenciones_sin_comprobante(self, periodo_id=None):
        """Retenciones sin comprobante físico recibido (estado_recepcion
        esperado/vencido — incluye también las "Declarado sin Comprobante",
        que antes desaparecían de este listado en cuanto se declaraban aunque
        el papel siguiera sin llegar).

        Devuelve lista de dicts serializable a JSON con partner, factura,
        montos y días transcurridos desde la fecha de la factura,
        ordenados por monto_retenido descendente.
        """
        from datetime import date as _date
        domain = [('estado_recepcion', 'in', ('esperado', 'vencido'))]
        if periodo_id:
            domain.append(('conciliacion_id', '=', periodo_id))
        recs = self.search(domain, order='monto_retenido desc')
        today = _date.today()
        result = []
        for r in recs:
            inv_date = r.invoice_id.invoice_date if r.invoice_id else None
            dias = (today - inv_date).days if inv_date else None
            result.append({
                'id': r.id,
                'ref': r.ref or '',
                'partner': r.partner_id.name if r.partner_id else '',
                'rif': r.rif or '',
                'numero_factura': r.invoice_id.name if r.invoice_id else (r.nro_documento or ''),
                'nro_control': r.nro_control or '',
                'periodo': r.periodo or '',
                'monto_retenido': r.monto_retenido,
                'monto_iva_16': r.monto_iva,
                'monto_iva_8': r.monto_iva_red,
                'state': r.state,
                'fuera_plazo': r.fuera_plazo,
                'dias_transcurridos': dias,
                'fecha_vencimiento': str(r.fecha_vencimiento_entrega) if r.fecha_vencimiento_entrega else None,
            })
        return result

    @api.model
    def get_analisis_periodo(self, fecha_desde=None, fecha_hasta=None):
        """Totales por estado para un rango de fechas de factura.

        Devuelve dict serializable a JSON con conteos y montos de retenciones
        emitidas, recibidas con comprobante, pendientes y % de cumplimiento.
        """
        domain = [('state', '!=', 'anulado')]
        if fecha_desde:
            domain.append(('invoice_id.invoice_date', '>=', fecha_desde))
        if fecha_hasta:
            domain.append(('invoice_id.invoice_date', '<=', fecha_hasta))
        recs = self.search(domain)
        recibidas = recs.filtered(lambda r: r.estado_recepcion in r._RECIBIDO_ESTADOS)
        pendientes = recs.filtered(lambda r: r.state in ('esperado', 'vencido'))
        total = len(recs)
        return {
            'total_emitidas': total,
            'total_recibidas_comprobante': len(recibidas),
            'total_pendientes_comprobante': len(pendientes),
            'monto_total_emitido': round(sum(recs.mapped('monto_retenido')), 2),
            'monto_recibido': round(sum(recibidas.mapped('monto_recibido')), 2),
            'monto_pendiente': round(sum(pendientes.mapped('monto_retenido')), 2),
            'porcentaje_cumplimiento': round(len(recibidas) / total * 100, 1) if total else 0.0,
            'fecha_desde': str(fecha_desde) if fecha_desde else None,
            'fecha_hasta': str(fecha_hasta) if fecha_hasta else None,
        }

    @api.model
    def get_proyeccion_flujo_iva(self, mes, anio):
        """Proyección del flujo de IVA para mes/año: débito fiscal vs retenciones.

        Calcula IVA débito fiscal desde facturas de venta del mes, resta
        retenciones recibidas y pendientes, y devuelve el IVA neto estimado
        a declarar al SENIAT. Serializable a JSON.
        """
        import calendar
        from datetime import date as _date
        last_day = calendar.monthrange(anio, mes)[1]
        fecha_ini = _date(anio, mes, 1)
        fecha_fin = _date(anio, mes, last_day)
        periodo = f'{anio:04d}-{mes:02d}'
        company_id = self.env.company.id

        facturas = self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', fecha_ini),
            ('invoice_date', '<=', fecha_fin),
            ('company_id', '=', company_id),
        ])
        debito_fiscal = round(sum(facturas.mapped('amount_tax')), 2)

        recs = self.search([('periodo', '=', periodo), ('state', '!=', 'anulado')])
        recibidas = recs.filtered(lambda r: r.estado_recepcion in r._RECIBIDO_ESTADOS)
        pendientes = recs.filtered(lambda r: r.state in ('esperado', 'vencido'))
        monto_recibido = round(sum(recibidas.mapped('monto_recibido')), 2)
        monto_pendiente = round(sum(pendientes.mapped('monto_retenido')), 2)

        return {
            'mes': mes,
            'anio': anio,
            'periodo': periodo,
            'total_facturas_venta': len(facturas),
            'debito_fiscal': debito_fiscal,
            'retenciones_recibidas': monto_recibido,
            'retenciones_pendientes': monto_pendiente,
            'total_retenciones': round(monto_recibido + monto_pendiente, 2),
            'iva_neto_estimado': round(debito_fiscal - monto_recibido - monto_pendiente, 2),
        }

    @api.model
    def get_comparativo_seniat(self, periodo_id):
        """Compara ve.wh.iva (Odoo) con ve.seniat.retencion (SENIAT) para un período.

        Identifica: solo_odoo (sin match SENIAT), solo_seniat (sin retención Odoo),
        con_diferencia (diferencia de monto), y conteo de conciliadas OK.
        Serializable a JSON.
        """
        periodo = self.env['ve.conciliacion.periodo'].browse(periodo_id)
        if not periodo.exists():
            return {'error': f'Período id={periodo_id} no encontrado'}

        def _fmt_wh(r):
            return {
                'id': r.id,
                'ref': r.ref or '',
                'partner': r.partner_id.name if r.partner_id else '',
                'rif': r.rif or '',
                'nro_control': r.nro_control or '',
                'monto_retenido': r.monto_retenido,
                'monto_seniat': r.monto_seniat,
                'diferencia': round(r.diferencia_monto, 2),
                'estado_conciliacion': r.estado_conciliacion,
            }

        def _fmt_seniat(s):
            return {
                'id': s.id,
                'rif_agente': s.rif_agente or '',
                'nombre_agente': s.nombre_agente or '',
                'nro_control': s.nro_control or '',
                'monto_retenido': s.monto_retenido,
                'estado': s.estado,
            }

        activos = periodo.wh_iva_ids.filtered(lambda r: r.state != 'anulado')
        _SENIAT_OK = frozenset({
            'listo_declarar', 'conciliada_norec', 'declarado', 'conciliada', 'aprobado_declarar'
        })
        return {
            'periodo': periodo.periodo_retencion,
            'solo_odoo': [_fmt_wh(r) for r in activos.filtered(
                lambda r: r.estado_conciliacion == 'solo_odoo')],
            'solo_seniat': [_fmt_seniat(s) for s in periodo.seniat_ids.filtered(
                lambda s: s.estado == 'sin_match')],
            'con_diferencia': [_fmt_wh(r) for r in activos.filtered(
                lambda r: r.estado_conciliacion == 'diferencia')],
            'total_conciliadas_ok': len(activos.filtered(
                lambda r: r.estado_conciliacion in _SENIAT_OK)),
            'total_odoo': round(sum(activos.mapped('monto_retenido')), 2),
            'total_seniat': round(sum(periodo.seniat_ids.mapped('monto_retenido')), 2),
            'diferencia_total': round(periodo.diferencia, 2),
        }

    @api.model
    def send_recordatorio_comprobante(self, partner_ids):
        """Envía recordatorio de comprobante pendiente para los partners indicados.

        Por cada retención en estado esperado/vencido de los partners dados,
        envía un email al contacto y registra en el chatter la auditoría
        del envío (vía _enviar_recordatorio_tipo).
        Devuelve dict con cantidad de recordatorios enviados y suma de montos.
        """
        pendientes = self.search([
            ('partner_id', 'in', partner_ids),
            ('state', 'in', ('esperado', 'vencido')),
        ])
        enviados = 0
        monto_total = 0.0
        partners_notificados = []
        for wh in pendientes:
            try:
                wh._enviar_recordatorio_tipo('envio_comp')
                enviados += 1
                monto_total += wh.monto_retenido
                nombre = wh.partner_id.name if wh.partner_id else ''
                if nombre and nombre not in partners_notificados:
                    partners_notificados.append(nombre)
            except Exception as exc:
                _logger.warning(
                    've.wh.iva.send_recordatorio_comprobante: error en wh %d: %s',
                    wh.id, exc,
                )
        return {
            'enviados': enviados,
            'monto_total_notificado': round(monto_total, 2),
            'partners_notificados': partners_notificados,
            'total_partners': len(partners_notificados),
        }

    @api.model
    def _cron_recordatorios_vencimiento(self):
        """Cron diario: envía recordatorio para comprobantes próximos a vencer.

        Configurable con el parámetro del sistema:
          ve_retencion_iva.dias_aviso_vencimiento  (default: 3)
        """
        from datetime import date, timedelta
        cfg = self.env['ir.config_parameter'].sudo()
        dias = int(cfg.get_param('ve_retencion_iva.dias_aviso_vencimiento', '3'))
        hoy = date.today()
        fecha_limite = hoy + timedelta(days=dias)

        pendientes = self.search([
            ('state', 'in', ('esperado', 'vencido')),
            ('fecha_vencimiento_entrega', '>=', str(hoy)),
            ('fecha_vencimiento_entrega', '<=', str(fecha_limite)),
            ('fecha_ultimo_recordatorio', '=', False),
        ])

        enviados = 0
        for wh in pendientes:
            wh._enviar_recordatorio_tipo('envio_comp')
            enviados += 1

        if enviados:
            _logger.info(
                've_retencion_iva cron: %d recordatorio(s) de vencimiento enviados '
                '(rango: %s → %s)',
                enviados, hoy, fecha_limite,
            )

    @api.model
    def _cron_actualizar_estado_vencido(self):
        """Cron diario: mantiene 'state' sincronizado con fecha_vencimiento_entrega
        en ambos sentidos, ya que nada más lo recalcula automáticamente:
          - esperado  → vencido  cuando ya pasó la fecha límite de entrega.
          - vencido   → esperado cuando la fecha límite quedó en el futuro
            (p.ej. porque cambió la fecha de la factura vinculada y el plazo
            recalculado ya no venció) — evita registros "Vencido" con fecha
            límite futura.
        """
        from datetime import date
        hoy = str(date.today())

        a_vencer = self.search([
            ('state', '=', 'esperado'),
            ('fecha_vencimiento_entrega', '<', hoy),
        ])
        if a_vencer:
            a_vencer.write({'state': 'vencido'})
            for wh in a_vencer:
                wh.message_post(
                    body='Estado cambiado a Vencido automáticamente: se cumplió '
                         'la fecha límite de entrega sin recibir el comprobante.',
                    message_type='comment', subtype_xmlid='mail.mt_note',
                )

        a_reabrir = self.search([
            ('state', '=', 'vencido'),
            ('fecha_vencimiento_entrega', '>=', hoy),
        ])
        if a_reabrir:
            a_reabrir.write({'state': 'esperado'})
            for wh in a_reabrir:
                wh.message_post(
                    body='Estado revertido a No Recibido automáticamente: la fecha '
                         'límite de entrega recalculada ya no está vencida.',
                    message_type='comment', subtype_xmlid='mail.mt_note',
                )

        if a_vencer or a_reabrir:
            _logger.info(
                've_retencion_iva cron: %d retención(es) marcadas Vencido, '
                '%d revertidas a No Recibido',
                len(a_vencer), len(a_reabrir),
            )
