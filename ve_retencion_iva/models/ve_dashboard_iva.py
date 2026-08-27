import math
import re
from datetime import date, timedelta
from html import escape

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

N_PUNTOS_SERIE = 6


class VeDashboardIva(models.Model):
    _name = 've.dashboard.iva'
    _description = 'Dashboard IVA Venezuela'

    name = fields.Char(default='Dashboard IVA Venezuela', readonly=True)

    # Expone res.company.ve_declarado_manual para poder ocultar en la vista
    # (invisible="ve_declarado_manual") lo que dependa de un cálculo de
    # Odoo que el contador ya decidió no usar para "Declarado" en esta
    # compañía -- pedido explícito 2026-08-22: Posición Neta SENIAT
    # siempre lee campo_90 (calculado por Odoo) sin importar este flag, así
    # que para un cliente en modo manual ese número puede no reflejar lo
    # que realmente declaró -- mejor no mostrarlo que mostrar un estimado
    # que podría no coincidir con la realidad del cliente.
    ve_declarado_manual = fields.Boolean(
        compute='_compute_ve_declarado_manual', store=False)

    # Informativo, no un dato propio del singleton (ve.dashboard.iva no
    # tiene company_id — ver _get_or_create_singleton, un solo registro
    # global) — refleja la compañía ACTIVA de la sesión en el momento en
    # que se abre/refresca el Dashboard, la misma que ya usan todos los
    # demás computes de este modelo (self.env.company.id) para filtrar
    # sus datos. Pedido explícito 2026-08-27: mostrarla junto al título
    # "Semáforo Operativo" para que quede claro de qué compañía son los
    # números, sobre todo en Multiempresa (ver MULTI-06 — el Dashboard no
    # combina varias compañías, solo muestra la activa).
    company_id = fields.Many2one(
        'res.company', compute='_compute_company_id', store=False)

    def _compute_company_id(self):
        for rec in self:
            rec.company_id = self.env.company

    def _compute_ve_declarado_manual(self):
        manual = self.env.company.ve_declarado_manual
        for rec in self:
            rec.ve_declarado_manual = manual

    # ── Semáforo operativo ────────────────────────────────────────────────────
    total_vencidos = fields.Integer(
        string='Comprobantes Vencidos',
        compute='_compute_semaforo', store=False)
    total_esperados = fields.Integer(
        string='Comprobantes Esperados',
        compute='_compute_semaforo', store=False)
    total_borrador = fields.Integer(
        string='Recibidos sin Confirmar',
        compute='_compute_semaforo', store=False)
    total_periodos_abiertos = fields.Integer(
        string='Períodos Sin Declarar (Prescripción)',
        compute='_compute_semaforo', store=False)

    # ── Checklist Operativo ───────────────────────────────────────────────────
    periodo_activo_name = fields.Char(
        compute='_compute_checklist', store=False)
    dias_cierre_quincena = fields.Integer(
        compute='_compute_checklist', store=False)
    retenciones_ok = fields.Integer(
        compute='_compute_checklist', store=False)
    retenciones_total = fields.Integer(
        compute='_compute_checklist', store=False)
    pct_retenciones_ok = fields.Float(
        compute='_compute_checklist', store=False, digits=(5, 1))
    estado_conciliacion_periodo = fields.Char(
        compute='_compute_checklist', store=False)

    # ── Gráfico Estimado / Recibido / Pendiente / Declarado / SENIAT / Conciliado ──
    estimado_recibido_svg_html = fields.Html(
        compute='_compute_estimado_recibido', store=False, sanitize=False)
    # Tabla Riesgo de Sanción (Declarado no Recibido) / Subdeclaración
    # (Recibido no Declarado), una fila por mes -- pedido explícito
    # 2026-08-10. Ver nota en _compute_estimado_recibido.
    riesgo_sancion_mensual_html = fields.Html(
        compute='_compute_estimado_recibido', store=False, sanitize=False)
    # Tarjetas IOC/TAC/BDS, universo PROYECCIÓN (Base = Retenciones
    # Esperadas YTD, mismo 75% que ya usa el gráfico de arriba) -- pedido
    # explícito 2026-08-11, mismo cálculo que ya se usaba en la propuesta
    # comercial en PowerPoint (scripts/demo_cementos/
    # gen_propuesta_cementos_pptx.py), ahora en vivo dentro de Odoo.
    ioc_tac_bds_html = fields.Html(
        compute='_compute_estimado_recibido', store=False, sanitize=False)

    # ── Cobranza vs. Comprobante (exposición actual) ─────────────────────────
    # Cruza payment_state/estado_cobranza (ve.wh.iva, ver ve_wh_iva.py) con el
    # período activo. "Pagado sin comprobante" es el riesgo real: el cliente
    # ya pagó (Art. 13 — la retención se practica al pago/abono) pero el
    # comprobante no ha llegado. Es una urgencia distinta de Antigüedad/
    # Vencidos (que miran plazo legal vencido, fecha_vencimiento_entrega):
    # aquí la marca el flujo de caja ya ocurrido, no el calendario.
    cobranza_en_transito_bs = fields.Float(
        string='Crédito Fiscal en Tránsito',
        compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))
    cobranza_en_transito_count = fields.Integer(
        string='Facturas Pagadas sin Comprobante',
        compute='_compute_cobranza_exposicion', store=False)
    cobranza_total_periodo_count = fields.Integer(
        compute='_compute_cobranza_exposicion', store=False)
    cobranza_total_periodo_bs = fields.Float(
        string='Total Crédito del Período',
        compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))
    cobranza_pct_no_confirmado = fields.Float(
        compute='_compute_cobranza_exposicion', store=False, digits=(5, 1))
    cobranza_gauge_html = fields.Html(
        compute='_compute_cobranza_exposicion', store=False, sanitize=False)
    # Doble riesgo: intersección de dos ejes distintos (vencido, por
    # fecha_vencimiento_entrega; Y en tránsito, por estado_cobranza) — un
    # mismo comprobante puede tener ambos a la vez. El caso más urgente:
    # el cliente ya pagó y además el plazo legal ya venció.
    cobranza_doble_riesgo_count = fields.Integer(
        compute='_compute_cobranza_exposicion', store=False)
    cobranza_doble_riesgo_bs = fields.Float(
        compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))

    # ── Crédito del Período por Estado (eje ciclo de vida) ───────────────────
    # Distinto del eje de Cobranza de arriba (pago vs. comprobante): estas 4
    # categorías son EXCLUYENTES entre sí y suman exactamente
    # cobranza_total_periodo_bs. No combinar ambos ejes en un solo gráfico —
    # un mismo comprobante puede ser "Vencido" en este eje y "En Tránsito"
    # en el de Cobranza al mismo tiempo (eso lo captura doble_riesgo arriba).
    estado_confirmado_bs = fields.Float(compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))
    estado_confirmado_count = fields.Integer(compute='_compute_cobranza_exposicion', store=False)
    estado_borrador_bs = fields.Float(compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))
    estado_borrador_count = fields.Integer(compute='_compute_cobranza_exposicion', store=False)
    estado_no_recibido_bs = fields.Float(compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))
    estado_no_recibido_count = fields.Integer(compute='_compute_cobranza_exposicion', store=False)
    estado_vencido_bs = fields.Float(compute='_compute_cobranza_exposicion', store=False, digits=(16, 2))
    estado_vencido_count = fields.Integer(compute='_compute_cobranza_exposicion', store=False)
    estado_periodo_bar_html = fields.Html(
        compute='_compute_cobranza_exposicion', store=False, sanitize=False)

    # Ranking de clientes (agentes de retención) por monto pendiente en
    # 'pagado_sin_comprobante'. Backlog GLOBAL, no solo el período activo a
    # propósito: un cliente incumplidor recurrente debe verse aunque esta
    # quincena tenga pocos casos nuevos (mismo criterio que aging_total_bs).
    top_clientes_html = fields.Html(
        compute='_compute_top_clientes', store=False, sanitize=False)
    pct_concentracion_top3 = fields.Float(
        compute='_compute_top_clientes', store=False, digits=(5, 1))

    # Ranking de Retenciones Pendientes por Zona/Planta — pedido explícito
    # 2026-08-01 (prospecto Cementos, 10 plantas bajo un único RIF): "cuánto
    # crédito fiscal puede recuperar y dónde enfocar el seguimiento" — mismo
    # criterio que top_clientes_html (backlog GLOBAL, no solo período
    # activo), pero agrupado por zona en vez de por cliente, y el badge de
    # color es % Vencido de esa zona (no concentración) — es la señal que
    # de verdad importa para decidir dónde llamar primero.
    zona_pendiente_html = fields.Html(
        compute='_compute_zona_pendiente', store=False, sanitize=False)

    # ── Antigüedad de comprobantes vencidos (aging) ──────────────────────────
    # Global (todos los períodos, no solo el activo) — el riesgo real de
    # pérdida del crédito fiscal no depende de si el comprobante es de la
    # quincena actual o de una anterior arrastrada.
    aging_0_15_count = fields.Integer(compute='_compute_aging', store=False)
    aging_0_15_bs = fields.Float(compute='_compute_aging', store=False, digits=(16, 2))
    aging_16_30_count = fields.Integer(compute='_compute_aging', store=False)
    aging_16_30_bs = fields.Float(compute='_compute_aging', store=False, digits=(16, 2))
    aging_31_mas_count = fields.Integer(compute='_compute_aging', store=False)
    aging_31_mas_bs = fields.Float(compute='_compute_aging', store=False, digits=(16, 2))
    aging_total_count = fields.Integer(compute='_compute_aging', store=False)
    aging_total_bs = fields.Float(compute='_compute_aging', store=False, digits=(16, 2))
    # Impacto: el monto en riesgo solo dice algo si se compara con el
    # Débito Fiscal — la misma referencia que ya usa "Cascada de
    # Liquidez" (pct_en_riesgo), mostrada aquí para no obligar a bajar a
    # buscarla. Período (solo comprobantes vencidos del período activo) y
    # YTD (año calendario en curso, MISMO alcance que pct_en_riesgo de
    # Cascada de Liquidez — no confundir con aging_total_bs, que sí es
    # todo el backlog histórico sin filtro de año).
    aging_periodo_bs = fields.Float(compute='_compute_aging', store=False, digits=(16, 2))
    aging_pct_debito_periodo = fields.Float(
        compute='_compute_aging', store=False, digits=(5, 1))
    aging_pct_debito_ytd = fields.Float(
        compute='_compute_aging', store=False, digits=(5, 1))
    # Medidores tipo "meter" (barra con umbrales de color) para el impacto
    # de período y YTD (medidor circular tipo velocímetro) — ver _gauge_html.
    aging_meter_periodo_html = fields.Html(compute='_compute_aging', store=False, sanitize=False)
    aging_meter_ytd_html = fields.Html(compute='_compute_aging', store=False, sanitize=False)
    # Barra horizontal apilada con la composición del riesgo (qué % es
    # reciente vs. crónico) — ver _aging_bar_html. Html/sanitize=False por
    # el mismo motivo que los demás gráficos: 100% generado en Python.
    aging_bar_html = fields.Html(compute='_compute_aging', store=False, sanitize=False)
    # Gráfico de barras verticales por bucket (uno por campo para que cada
    # una pueda envolverse en su propio <button type="object"> clickable
    # en la vista — un solo Html no permite botones independientes por
    # segmento). Altura proporcional al monto (Bs), no a la cantidad.
    aging_bar_0_15_html = fields.Html(compute='_compute_aging', store=False, sanitize=False)
    aging_bar_16_30_html = fields.Html(compute='_compute_aging', store=False, sanitize=False)
    aging_bar_31_mas_html = fields.Html(compute='_compute_aging', store=False, sanitize=False)

    # ── Lista de Trabajo (comprobantes con acción de recordatorio pendiente) ─
    # Reusa los mismos 3 botones/condiciones de recordatorio que ya existen
    # en "Visual IVA Clientes" (ve_wh_iva_view_concil_list) — no se
    # introduce lógica de negocio nueva, solo se expone en el Dashboard.
    lista_trabajo_ids = fields.Many2many(
        've.wh.iva', compute='_compute_lista_trabajo', store=False,
        string='Lista de Trabajo')

    # ── KPI 1: Margen Crédito / Débito ───────────────────────────────────────
    periodo_ref_name = fields.Char(
        string='Último Período', compute='_compute_margen_cd', store=False)
    margen_cd_periodo = fields.Float(
        string='Margen C/D Período', compute='_compute_margen_cd', store=False, digits=(5, 1))
    margen_cd_ytd = fields.Float(
        string='Margen C/D YTD', compute='_compute_margen_cd', store=False, digits=(5, 1))
    # Nombre corregido 2026-07-21: este campo guarda campo_39 (Créditos
    # Fiscales), NUNCA guardó campo_66 — el nombre anterior (campo_66_periodo)
    # era incorrecto y confundió el diseño de Posición Neta (ver abajo).
    credito_fiscal_periodo = fields.Float(
        string='Crédito Fiscal Período (Bs)', compute='_compute_margen_cd', store=False, digits=(16, 0))
    campo_49_periodo = fields.Float(
        string='Débito Fiscal Período (Bs)', compute='_compute_margen_cd', store=False, digits=(16, 0))
    credito_fiscal_ytd = fields.Float(
        string='Crédito Fiscal YTD (Bs)', compute='_compute_margen_cd', store=False, digits=(16, 0))
    campo_49_ytd = fields.Float(
        string='Débito Fiscal YTD (Bs)', compute='_compute_margen_cd', store=False, digits=(16, 0))
    periodo_ref_id = fields.Integer(
        string='ID Período Ref', compute='_compute_margen_cd', store=False)

    # Riesgo de declaración: qué % del crédito que se va a declarar este
    # período (Campo 66, ve.declaracion.iva) corresponde a comprobantes
    # todavía en 'esperado'/'vencido' — o sea, declarado por el monto
    # ESPERADO porque incluir_declaracion=True, sin que el comprobante
    # físico haya llegado aún (ver monto_c66 en ve_wh_iva.py). No es un
    # error del sistema — es una decisión de negocio existente (declarar
    # provisionalmente) — pero es exactamente el riesgo que puede forzar
    # una declaración sustitutiva (REQ-10) si el comprobante nunca llega o
    # llega con diferencia de monto.
    c66_total_periodo_bs = fields.Float(
        compute='_compute_riesgo_declaracion', store=False, digits=(16, 2))
    c66_sin_confirmar_periodo_bs = fields.Float(
        string='Declarado sin Comprobante Confirmado (Período)',
        compute='_compute_riesgo_declaracion', store=False, digits=(16, 2))
    pct_c66_sin_confirmar_periodo = fields.Float(
        compute='_compute_riesgo_declaracion', store=False, digits=(5, 1))
    c66_total_ytd_bs = fields.Float(
        compute='_compute_riesgo_declaracion', store=False, digits=(16, 2))
    c66_sin_confirmar_ytd_bs = fields.Float(
        string='Declarado sin Comprobante Confirmado (YTD)',
        compute='_compute_riesgo_declaracion', store=False, digits=(16, 2))
    pct_c66_sin_confirmar_ytd = fields.Float(
        compute='_compute_riesgo_declaracion', store=False, digits=(5, 1))

    # ── KPI: Declarado (C.66) vs. SENIAT (antes "Posición Neta SENIAT") ──────
    # Replanteado 2026-08-27 (ver _compute_posicion_neta para el detalle
    # completo): la versión anterior (2026-07-21) era un espejo exacto del
    # Campo 90 (ve.declaracion.iva.campo_90) -- 100% autoreferencial a la
    # propia declaración, sin cruzar nada contra el SENIAT real. Ahora
    # compara Campo 66 Declarado vs. total_seniat (lo que los clientes
    # reportaron al portal). Positivo = Declarado > SENIAT (riesgo, crédito
    # sin respaldo); negativo = SENIAT > Declarado (oportunidad, crédito sin
    # aprovechar).
    posicion_neta_periodo_bs = fields.Float(
        compute='_compute_posicion_neta', store=False, digits=(16, 2))
    posicion_neta_periodo_label = fields.Char(
        compute='_compute_posicion_neta', store=False)
    posicion_neta_ytd_bs = fields.Float(
        compute='_compute_posicion_neta', store=False, digits=(16, 2))
    posicion_neta_ytd_label = fields.Char(
        compute='_compute_posicion_neta', store=False)

    # Brecha: información complementaria e independiente del KPI de arriba
    # (Declarado vs. SENIAT) — cuánto crédito todavía podría entrar (No
    # Recibido + Recibido sin Confirmar + Vencido, los 3 buckets de "Crédito
    # del Período por Estado" que no son Confirmado/Recibido) y cómo pesa
    # eso frente al Débito Fiscal.
    brecha_pendiente_bs = fields.Float(
        compute='_compute_brecha', store=False, digits=(16, 2))
    brecha_pendiente_count = fields.Integer(
        compute='_compute_brecha', store=False)
    brecha_pendiente_pct = fields.Float(
        compute='_compute_brecha', store=False, digits=(5, 1))
    brecha_gauge_html = fields.Html(
        compute='_compute_brecha', store=False, sanitize=False)

    # ── KPI Nuevo: Excedente de Crédito Fiscal Acumulado ──────────────────────
    # "Trasladable actual" = campo_60 de la Declaración IVA del período
    # activo — el excedente de CRÉDITO FISCAL (C.39 > C.49, compras por
    # encima de ventas) que se auto-copia como C.20 del período siguiente
    # (_auto_copiar_arrastre). Corregido 2026-07-24: antes leía
    # campo_54_arrastre (nombre viejo: campo_33_arrastre), que en realidad es
    # el arrastre de RETENCIONES no descontadas (C.54) — un pozo legal
    # distinto, ya cubierto por su propio campo `saldo_nuevo_campo_54`
    # (nombre viejo: saldo_nuevo_campo_33; sin KPI propio en el dashboard
    # todavía).
    # Antigüedad promedio y erosión BCV quedan pendientes: requieren
    # reconstruir históricamente cuándo se generó cada capa del excedente y
    # a qué tasa BCV estaba vigente entonces (campo_60 es un total corrido,
    # no lleva ese detalle) — no implementado en esta ronda.
    excedente_trasladable_bs = fields.Float(
        compute='_compute_excedente', store=False, digits=(16, 2))
    excedente_periodo_ref_name = fields.Char(
        compute='_compute_excedente', store=False)
    excedente_tendencia_pct = fields.Float(
        compute='_compute_excedente', store=False, digits=(5, 1))

    # ── KPI Nuevo: Conciliación de Datos vs. SENIAT (4 vías) ──────────────────
    # Cantidad Y monto de cada categoría (pedido explícito 2026-07-31: la
    # versión anterior solo mostraba %, sin valores) — Período Activo y YTD
    # lado a lado, mismo patrón ya usado en Posición Neta/Tasa Efectiva de
    # este mismo dashboard. Ver _calc_concil_buckets (cálculo compartido).
    concil_conciliadas_count = fields.Integer(
        compute='_compute_salud_conciliacion', store=False)
    concil_solo_odoo_count = fields.Integer(
        compute='_compute_salud_conciliacion', store=False)
    concil_solo_seniat_count = fields.Integer(
        compute='_compute_salud_conciliacion', store=False)
    concil_con_diferencia_count = fields.Integer(
        compute='_compute_salud_conciliacion', store=False)
    concil_sin_conciliar_count = fields.Integer(
        compute='_compute_salud_conciliacion', store=False)
    concil_bar_html = fields.Html(
        compute='_compute_salud_conciliacion', store=False, sanitize=False)
    concil_bar_html_ytd = fields.Html(
        compute='_compute_salud_conciliacion', store=False, sanitize=False)

    # ── KPI 2: Recuperación de Crédito por Retención (antes "Tasa Efectiva
    # de Retención") -- renombrado 2026-08-27, mismo cálculo, el nombre
    # anterior sonaba a "% legal de retención" (75%/100%, ver
    # porcentaje_retencion) cuando en realidad mide recuperación real de
    # crédito confirmado sobre IVA Causado.
    tasa_ef_periodo = fields.Float(
        string='Recuperación Período (%)', compute='_compute_tasa_ef', store=False, digits=(5, 1))
    tasa_ef_ytd = fields.Float(
        string='Recuperación YTD (%)', compute='_compute_tasa_ef', store=False, digits=(5, 1))
    tasa_anio = fields.Integer(
        string='Año', compute='_compute_tasa_ef', store=False)
    # Montos crudos del cálculo (numerador/denominador), mismo patrón que
    # Créd./Déb. Fiscal en Margen C/D — sin esto el % no se puede auditar
    # a simple vista (confundió a la usuaria con 1 solo comprobante 60k/75%).
    tasa_ef_retenido_periodo = fields.Float(
        string='Retenido Confirmado Período (Bs)', compute='_compute_tasa_ef', store=False, digits=(16, 0))
    tasa_ef_causado_periodo = fields.Float(
        string='IVA Causado Período (Bs)', compute='_compute_tasa_ef', store=False, digits=(16, 0))
    tasa_ef_retenido_ytd = fields.Float(
        string='Retenido Confirmado YTD (Bs)', compute='_compute_tasa_ef', store=False, digits=(16, 0))
    tasa_ef_causado_ytd = fields.Float(
        string='IVA Causado YTD (Bs)', compute='_compute_tasa_ef', store=False, digits=(16, 0))

    # ── KPI 3: Cumplimiento SPE ───────────────────────────────────────────────
    pct_cumpl_4q = fields.Float(
        string='Cumplimiento últimas 4 quincenas (%)',
        compute='_compute_cumplimiento', store=False, digits=(5, 1))
    pct_cumpl_12m = fields.Float(
        string='Cumplimiento últimos 12 meses (%)',
        compute='_compute_cumplimiento', store=False, digits=(5, 1))
    periodos_en_plazo_4q = fields.Integer(
        compute='_compute_cumplimiento', store=False)
    periodos_eval_4q = fields.Integer(
        compute='_compute_cumplimiento', store=False)
    periodos_en_plazo_12m = fields.Integer(
        compute='_compute_cumplimiento', store=False)
    periodos_eval_12m = fields.Integer(
        compute='_compute_cumplimiento', store=False)

    # ── KPI 4: Sanciones del Año ──────────────────────────────────────────────
    # Separadas por estado (Pendientes vs Impugnadas). Todas las sanciones
    # IVA se fijan en la moneda de mayor valor BCV (normalmente EUR, Art.
    # 96/98/108 COT) — el Bs se recalcula con la tasa vigente hasta que se
    # pagan (ver monto_bs_hoy en ve.sancion.iva.line). Solo suma sanciones
    # Pendiente/Impugnada (no Pagada ni Prescrita).
    total_sanciones_ano_bs = fields.Float(
        string='Sanciones Año (Bs)',
        compute='_compute_sanciones', store=False, digits=(16, 2))
    total_sanciones_ano_eur = fields.Float(
        string='Sanciones Año (EUR)',
        compute='_compute_sanciones', store=False, digits=(16, 2))
    sancion_pend_bs = fields.Float(compute='_compute_sanciones', store=False, digits=(16, 2))
    sancion_pend_eur = fields.Float(compute='_compute_sanciones', store=False, digits=(16, 2))
    sancion_impu_bs = fields.Float(compute='_compute_sanciones', store=False, digits=(16, 2))
    sancion_impu_eur = fields.Float(compute='_compute_sanciones', store=False, digits=(16, 2))
    # Mini gráfico de 2 barras (Pendientes vs Impugnadas) — ver
    # _sanciones_bars_html. Html/sanitize=False por el mismo motivo que los
    # sparklines: contenido 100% generado en Python, sin datos de usuario.
    sanciones_bars_html = fields.Html(compute='_compute_sanciones', store=False, sanitize=False)

    # ── Sparklines embebidos en las tarjetas KPI 1-3 (últimas 6 quincenas) ───
    # Se arman como Html (sanitize=False, contenido 100% generado en Python,
    # sin datos de usuario) porque un form view no tiene el "record.x.value"
    # de kanban disponible para interpolar SVG dinámico directo en el arch.
    margen_svg_html = fields.Html(compute='_compute_sparklines', store=False, sanitize=False)
    tasa_svg_html = fields.Html(compute='_compute_sparklines', store=False, sanitize=False)
    cumpl_svg_html = fields.Html(compute='_compute_sparklines', store=False, sanitize=False)

    # ── Crédito Fiscal SENIAT sin match ──────────────────────────────────────
    # KPI pedido 2026-08-05, pensado para el pitch a prospectos: monto que
    # el propio SENIAT ya confirmó a favor del cliente pero que NUNCA llegó
    # a vincularse con ningún registro de Odoo. Calculado EN VIVO por
    # RIF+N°Control (ver _solo_seniat_sin_match_bs) -- NO depende del campo
    # `estado` de ve.seniat.retencion, que solo pasa a 'sin_match' si
    # alguien ya corrió "Conciliar SENIAT" (la mayoría de los períodos
    # reales todavía no pasan por ahí). Backlog completo (todos los
    # períodos), no restringido al año calendario.
    solo_seniat_ytd_bs = fields.Float(
        compute='_compute_solo_seniat_sin_match', store=False, digits=(16, 2))
    seniat_total_bs = fields.Float(
        compute='_compute_solo_seniat_sin_match', store=False, digits=(16, 2))
    seniat_match_bars_html = fields.Html(
        compute='_compute_solo_seniat_sin_match', store=False, sanitize=False)
    resumen_ytd_bars_html = fields.Html(
        compute='_compute_resumen_ytd', store=False, sanitize=False,
        help='6 barras YTD (Esperadas/Recibido/Pendiente/Declarado/SENIAT/'
             'Conciliado, año en curso) -- pedido explícito 2026-08-14, va '
             'a la izquierda de "Crédito Fiscal SENIAT sin match" dentro de '
             'la sección "RESUMEN YTD". Mismo criterio y colores que el '
             'gráfico mensual de _compute_estimado_recibido, pero un solo '
             'total agregado por categoría en vez de una barra por mes.')

    # ── Cascada de Liquidez — Período y YTD ──────────────────────────────────
    debito_fiscal_cascade = fields.Float(
        compute='_compute_liquidez', store=False, digits=(16, 0))
    retenido_con_comprobante = fields.Float(
        compute='_compute_liquidez', store=False, digits=(16, 0))
    retenido_sin_comprobante = fields.Float(
        compute='_compute_liquidez', store=False, digits=(16, 0))
    pct_recuperado = fields.Float(
        compute='_compute_liquidez', store=False, digits=(5, 1))
    pct_en_riesgo = fields.Float(
        compute='_compute_liquidez', store=False, digits=(5, 1))
    debito_fiscal_periodo = fields.Float(
        compute='_compute_liquidez', store=False, digits=(16, 0))
    retenido_con_periodo = fields.Float(
        compute='_compute_liquidez', store=False, digits=(16, 0))
    retenido_sin_periodo = fields.Float(
        compute='_compute_liquidez', store=False, digits=(16, 0))
    retenido_sin_periodo_count = fields.Integer(
        compute='_compute_liquidez', store=False)
    pct_recuperado_periodo = fields.Float(
        compute='_compute_liquidez', store=False, digits=(5, 1))
    pct_en_riesgo_periodo = fields.Float(
        compute='_compute_liquidez', store=False, digits=(5, 1))

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _get_periodo_activo(self):
        today = fields.Date.today()
        periodo = self.env['ve.conciliacion.periodo'].search([
            ('fecha_inicio', '<=', today),
            ('fecha_fin', '>=', today),
            ('company_id', '=', self.env.company.id),
        ], order='fecha_fin desc', limit=1)
        if not periodo:
            periodo = self.env['ve.conciliacion.periodo'].search([
                ('estado', '!=', 'declarado'),
                ('company_id', '=', self.env.company.id),
            ], order='fecha_fin desc', limit=1)
        return periodo

    def _get_rango_ytd(self):
        # Vuelto a dinámico 2026-08-27 -- el rango fijo Ene-Jun 2026
        # (pedido puntual 2026-08-14 para no mezclar el Libro de Ventas
        # validado de Cementos, capado a esos meses, con datos reales
        # posteriores) quedó desactualizado exactamente como advertía el
        # comentario original: en Multiempresa (data de agosto) dejaba
        # todos los KPI "YTD" en 0 al caer fuera de esa ventana fija.
        # Cementos está congelado desde el 16-ago (su Odoo.sh ya ni
        # resuelve) y dejó de sincronizarse -- no hay ya ningún cliente
        # activo que dependa del rango fijo, así que se revierte al
        # cálculo dinámico real: 1 de enero del año en curso hasta hoy.
        hoy = fields.Date.today()
        return hoy.replace(month=1, day=1), hoy

    def _cumplimiento_en_rango(self, cutoff):
        # Total = todos los períodos en el rango (declarados o no)
        todos = self.env['ve.conciliacion.periodo'].search([
            ('fecha_fin', '>=', cutoff),
            ('company_id', '=', self.env.company.id),
        ])
        total = len(todos)
        # Declarados a tiempo (fecha_declaracion <= fecha_fin + 7 días)
        # Sin fecha_vencimiento_rif oficial se usa fecha_fin + 7 como proxy
        en_plazo = sum(
            1 for p in todos
            if p.estado == 'declarado'
            and p.declaracion_iva_id
            and p.declaracion_iva_id.fecha_declaracion
            and p.fecha_fin
            and p.declaracion_iva_id.fecha_declaracion.date() <= p.fecha_fin + timedelta(days=7)
        )
        pct = (en_plazo / total * 100) if total > 0 else 0.0
        return pct, en_plazo, total

    # ── Compute: semáforo ─────────────────────────────────────────────────────
    @api.depends()
    def _compute_semaforo(self):
        WH = self.env['ve.wh.iva']
        fecha_corte = fields.Date.today() - relativedelta(years=6)
        for rec in self:
            # Por fecha, no solo por state='vencido': el cron diario que
            # pasa esperado→vencido puede tener hasta ~24h de rezago frente
            # a la fecha límite real. Contar por fecha_vencimiento_entrega
            # mantiene este número siempre consistente con el total del
            # bloque de Antigüedad (mismo criterio, misma cifra).
            rec.total_vencidos = WH.search_count([
                ('state', 'in', ('esperado', 'vencido')),
                ('fecha_vencimiento_entrega', '<=', fields.Date.today()),
                ('company_id', '=', self.env.company.id),
            ])
            periodo = rec._get_periodo_activo()
            rec.total_borrador = WH.search_count([
                ('state', '=', 'borrador'),
                ('conciliacion_id', '=', periodo.id if periodo else False),
            ])
            # "No Recibido" = todo lo que sigue en state='esperado', sin
            # filtro de fecha: es el pipeline completo de comprobantes que
            # todavía no llegan, no solo los ya vencidos (eso ya lo cubre
            # "Vencidos", que sí filtra por fecha_vencimiento_entrega). La
            # urgencia se ve ordenando la lista por fecha límite, no
            # ocultando los que aún no vencen.
            rec.total_esperados = WH.search_count([
                ('state', '=', 'esperado'),
                ('company_id', '=', self.env.company.id),
            ])
            rec.total_periodos_abiertos = self.env['ve.conciliacion.periodo'].search_count([
                ('estado', '!=', 'declarado'),
                ('fecha_fin', '>=', fecha_corte),
                ('company_id', '=', self.env.company.id),
            ])

    # ── Compute: checklist operativo ──────────────────────────────────────────
    @api.depends()
    def _compute_checklist(self):
        estados_label = dict(
            self.env['ve.conciliacion.periodo']._fields['estado'].selection
        )
        for rec in self:
            periodo = rec._get_periodo_activo()
            if not periodo:
                rec.periodo_activo_name = 'Sin período activo'
                rec.dias_cierre_quincena = -99
                rec.retenciones_ok = 0
                rec.retenciones_total = 0
                rec.pct_retenciones_ok = 0.0
                rec.estado_conciliacion_periodo = ''
                continue

            rec.periodo_activo_name = periodo.periodo_retencion or periodo.name or '—'
            rec.dias_cierre_quincena = (
                (periodo.fecha_fin - fields.Date.today()).days
                if periodo.fecha_fin else -99
            )
            total = self.env['ve.wh.iva'].search_count([
                ('conciliacion_id', '=', periodo.id),
            ])
            ok = self.env['ve.wh.iva'].search_count([
                ('conciliacion_id', '=', periodo.id),
                ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
            ])
            rec.retenciones_ok = ok
            rec.retenciones_total = total
            rec.pct_retenciones_ok = (ok / total * 100) if total > 0 else 0.0
            rec.estado_conciliacion_periodo = estados_label.get(periodo.estado, periodo.estado or '')

    def _donut_html(self, pct, w=72):
        """Anillo simple (sin aguja) para un porcentaje 0-100 — usado en
        Exposición por Cobranza. Distinto de _gauge_html (velocímetro que
        compara contra Débito Fiscal con bandas fijas): aquí no hay una
        referencia externa que mostrar, el color depende solo del propio
        valor. Umbrales calibrados para esta métrica en particular: dentro
        de los 2 días hábiles que da el Art. 16, es normal que buena parte
        de lo ya pagado en la quincena aún no tenga comprobante — el umbral
        de alerta es más alto que en Antigüedad/Cascada (10/25%)."""
        r = w / 2 - 4.5
        circunferencia = 2 * math.pi * r
        pct_clamped = max(0.0, min(pct, 100.0))
        offset = circunferencia * (1 - pct_clamped / 100)
        if pct_clamped < 30:
            color = '#198754'
        elif pct_clamped < 60:
            color = '#fd7e14'
        else:
            color = '#dc3545'
        cx = cy = w / 2
        return (
            f'<svg width="{w}" height="{w}" viewBox="0 0 {w} {w}">'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="#EBFFFF" stroke-width="9"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="9" '
            f'stroke-dasharray="{circunferencia:.1f}" stroke-dashoffset="{offset:.1f}" '
            f'stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>'
            f'<text x="{cx}" y="{cy + 5:.1f}" text-anchor="middle" font-size="15" font-weight="700" '
            f'fill="{color}">{pct_clamped:.0f}%</text>'
            '</svg>'
        )

    def _estado_periodo_html(self, buckets, h=22):
        """Barra horizontal apilada + leyenda para 'Crédito del Período por
        Estado' (4 categorías excluyentes del ciclo de vida del
        comprobante). buckets: lista de (label, bs, count, color). No
        confundir con _aging_bar_html (ese es Antigüedad, solo vencidos,
        eje distinto — ver comentario en el campo estado_periodo_bar_html)."""
        total = sum(bs for _, bs, _, _ in buckets)
        if total <= 0:
            return (
                '<div class="text-muted" style="font-size:0.8rem; padding:4px 0;">'
                'Sin comprobantes en el período</div>'
            )
        segmentos = []
        leyenda = []
        for label, bs, count, color in buckets:
            pct = (bs / total * 100) if total > 0 else 0.0
            if bs > 0:
                segmentos.append(
                    f'<div style="width:{pct:.1f}%; height:{h}px; background-color:{color};" '
                    f'title="{label}: {count} comp. — Bs.{self._fmt_monto(bs)} ({pct:.0f}%)"></div>'
                )
            leyenda.append(
                '<div class="d-flex align-items-center gap-1">'
                f'<span style="width:8px; height:8px; border-radius:50%; background-color:{color}; '
                'display:inline-block; flex:none;"></span>'
                f'<span class="text-muted">{escape(label)}</span>'
                f'<span class="fw-bold">{pct:.0f}%</span>'
                '</div>'
            )
        return (
            f'<div class="d-flex rounded overflow-hidden mb-2" style="height:{h}px;">'
            + ''.join(segmentos) +
            '</div>'
            '<div class="d-flex flex-wrap gap-3" style="font-size:0.72rem;">'
            + ''.join(leyenda) +
            '</div>'
        )

    def _conteo_bar_html(self, buckets, h=22):
        """Barra horizontal apilada + leyenda por CANTIDAD (no monto) —
        usada en Conciliación de Datos vs. SENIAT (4 vías: conciliada/solo
        Odoo/solo SENIAT/con diferencia). buckets: lista de (label, count,
        color). Mismo estilo visual que _estado_periodo_html, pero ese
        pondera por Bs — aquí no aplica, la conciliación es por comprobante,
        no por monto."""
        total = sum(count for _, count, _ in buckets)
        if total <= 0:
            return (
                '<div class="text-muted" style="font-size:0.8rem; padding:4px 0;">'
                'Sin comprobantes en el período</div>'
            )
        segmentos = []
        leyenda = []
        for label, count, color in buckets:
            pct = (count / total * 100) if total > 0 else 0.0
            if count > 0:
                segmentos.append(
                    f'<div style="width:{pct:.1f}%; height:{h}px; background-color:{color};" '
                    f'title="{label}: {count} comp. ({pct:.0f}%)"></div>'
                )
            leyenda.append(
                '<div class="d-flex align-items-center gap-1">'
                f'<span style="width:8px; height:8px; border-radius:50%; background-color:{color}; '
                'display:inline-block; flex:none;"></span>'
                f'<span class="text-muted">{escape(label)}</span>'
                f'<span class="fw-bold">{pct:.0f}%</span>'
                '</div>'
            )
        return (
            f'<div class="d-flex rounded overflow-hidden mb-2" style="height:{h}px;">'
            + ''.join(segmentos) +
            '</div>'
            '<div class="d-flex flex-wrap gap-3" style="font-size:0.72rem;">'
            + ''.join(leyenda) +
            '</div>'
        )

    def _conteo_donut_html(self, buckets, w=130):
        """Doughnut multi-segmento por CANTIDAD y MONTO — anillo con leyenda
        al lado. buckets: lista de (label, count, monto, color). Centro
        muestra el total de comprobantes.

        Ampliado 2026-07-31 (pedido explícito): antes la leyenda solo
        mostraba %, sin cantidad ni monto — se agregó ambos para que el
        cliente vea de un vistazo cuánto crédito fiscal representa cada
        categoría, no solo la proporción."""
        total = sum(count for _, count, _, _ in buckets)
        if total <= 0:
            return (
                '<div class="text-muted" style="font-size:0.8rem; padding:4px 0;">'
                'Sin comprobantes en el período</div>'
            )
        r = w / 2 - 11
        circunferencia = 2 * math.pi * r
        cx = cy = w / 2
        cumulative = 0.0
        arcos = []
        leyenda = []
        for label, count, monto, color in buckets:
            pct = (count / total * 100) if total > 0 else 0.0
            if count > 0:
                length = circunferencia * (pct / 100)
                arcos.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" '
                    f'stroke-width="16" stroke-dasharray="{length:.2f} {circunferencia - length:.2f}" '
                    f'stroke-dashoffset="{-cumulative:.2f}" transform="rotate(-90 {cx} {cy})">'
                    f'<title>{escape(label)}: {count} comp. — Bs.{self._fmt_monto(monto)} '
                    f'({pct:.0f}%)</title></circle>'
                )
                cumulative += length
            leyenda.append(
                '<div class="d-flex align-items-center gap-1 flex-wrap">'
                f'<span style="width:8px; height:8px; border-radius:50%; background-color:{color}; '
                'display:inline-block; flex:none;"></span>'
                f'<span class="text-muted">{escape(label)}</span>'
                f'<span class="fw-bold">{count}</span>'
                f'<span class="text-muted">— Bs.{self._fmt_monto(monto)}</span>'
                f'<span class="fw-bold">({pct:.0f}%)</span>'
                '</div>'
            )
        svg = (
            f'<svg width="{w}" height="{w}" viewBox="0 0 {w} {w}">'
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="#EBEEF2" stroke-width="16"/>'
            + ''.join(arcos) +
            f'<text x="{cx}" y="{cy - 1:.1f}" text-anchor="middle" font-size="20" font-weight="700" '
            f'fill="#333">{total}</text>'
            f'<text x="{cx}" y="{cy + 16:.1f}" text-anchor="middle" font-size="10" '
            f'fill="#8a8f98">comp.</text>'
            '</svg>'
        )
        return (
            '<div class="d-flex align-items-center gap-3 flex-wrap">'
            f'<div style="flex:none;">{svg}</div>'
            '<div class="d-flex flex-column gap-1" style="font-size:0.72rem;">'
            + ''.join(leyenda) +
            '</div>'
            '</div>'
        )

    # ── Compute: Cobranza vs. Comprobante (exposición actual) ────────────────
    @api.depends()
    def _compute_cobranza_exposicion(self):
        WH = self.env['ve.wh.iva']
        hoy = fields.Date.today()
        for rec in self:
            periodo = rec._get_periodo_activo()
            if not periodo:
                rec.cobranza_en_transito_bs = 0.0
                rec.cobranza_en_transito_count = 0
                rec.cobranza_total_periodo_count = 0
                rec.cobranza_total_periodo_bs = 0.0
                rec.cobranza_pct_no_confirmado = 0.0
                rec.cobranza_gauge_html = rec._donut_html(0.0)
                rec.cobranza_doble_riesgo_count = 0
                rec.cobranza_doble_riesgo_bs = 0.0
                rec.estado_confirmado_bs = rec.estado_borrador_bs = 0.0
                rec.estado_no_recibido_bs = rec.estado_vencido_bs = 0.0
                rec.estado_confirmado_count = rec.estado_borrador_count = 0
                rec.estado_no_recibido_count = rec.estado_vencido_count = 0
                rec.estado_periodo_bar_html = rec._estado_periodo_html([])
                continue
            del_periodo = WH.search([
                ('conciliacion_id', '=', periodo.id),
                ('state', '!=', 'anulado'),
            ])
            sin_comp = del_periodo.filtered(lambda r: r.estado_cobranza == 'pagado_sin_comprobante')
            bs_total = sum(del_periodo.mapped('monto_retenido'))
            bs_sin = sum(sin_comp.mapped('monto_retenido'))
            rec.cobranza_en_transito_bs = bs_sin
            rec.cobranza_en_transito_count = len(sin_comp)
            rec.cobranza_total_periodo_count = len(del_periodo)
            rec.cobranza_total_periodo_bs = bs_total
            rec.cobranza_pct_no_confirmado = (bs_sin / bs_total * 100) if bs_total > 0 else 0.0
            rec.cobranza_gauge_html = rec._donut_html(rec.cobranza_pct_no_confirmado)

            # Crédito del Período por Estado — 4 categorías excluyentes,
            # mismo dominio de "Vencido" que usa el resto del Dashboard
            # (fecha_vencimiento_entrega <= hoy, no state=='vencido' solo,
            # por el rezago del cron — ver action_ver_vencidos).
            confirmado = del_periodo.filtered(lambda r: r.estado_recepcion in ('confirmado', 'confirmado_dif'))
            borrador = del_periodo.filtered(lambda r: r.state == 'borrador')
            vencido = del_periodo.filtered(
                lambda r: r.state in ('esperado', 'vencido')
                and r.fecha_vencimiento_entrega and r.fecha_vencimiento_entrega <= hoy)
            no_recibido = del_periodo.filtered(
                lambda r: r.state == 'esperado'
                and (not r.fecha_vencimiento_entrega or r.fecha_vencimiento_entrega > hoy))

            rec.estado_confirmado_bs = sum(confirmado.mapped('monto_retenido'))
            rec.estado_confirmado_count = len(confirmado)
            rec.estado_borrador_bs = sum(borrador.mapped('monto_retenido'))
            rec.estado_borrador_count = len(borrador)
            rec.estado_no_recibido_bs = sum(no_recibido.mapped('monto_retenido'))
            rec.estado_no_recibido_count = len(no_recibido)
            rec.estado_vencido_bs = sum(vencido.mapped('monto_retenido'))
            rec.estado_vencido_count = len(vencido)
            rec.estado_periodo_bar_html = rec._estado_periodo_html([
                ('Confirmado/Recibido', rec.estado_confirmado_bs, len(confirmado), '#198754'),
                ('Recibido sin Confirmar', rec.estado_borrador_bs, len(borrador), '#6c9bd1'),
                ('No Recibido', rec.estado_no_recibido_bs, len(no_recibido), '#ffc107'),
                ('Vencido', rec.estado_vencido_bs, len(vencido), '#dc3545'),
            ])

            doble_riesgo = vencido.filtered(lambda r: r.estado_cobranza == 'pagado_sin_comprobante')
            rec.cobranza_doble_riesgo_count = len(doble_riesgo)
            rec.cobranza_doble_riesgo_bs = sum(doble_riesgo.mapped('monto_retenido'))

    def _top_clientes_html(self, ranking, conteo, total_bs, top_n=3):
        """Tabla de los clientes con mayor monto pendiente en
        'pagado_sin_comprobante'. Semáforo por concentración individual
        sobre el total pendiente, mismos umbrales 10/25% que usa
        _gauge_html en el resto del Dashboard (no introduce un criterio de
        color nuevo)."""
        if not ranking:
            return (
                '<div class="text-muted" style="font-size:0.8rem; padding:8px 0;">'
                'Sin facturas pagadas pendientes de comprobante</div>'
            )
        filas = [(p.name or '—', monto, conteo[p]) for p, monto in ranking[:top_n]]
        resto = ranking[top_n:]
        if resto:
            filas.append((
                f'Otros ({len(resto)})',
                sum(m for _, m in resto),
                sum(conteo[p] for p, _ in resto),
            ))
        rows = []
        for nombre, monto, n in filas:
            pct = (monto / total_bs * 100) if total_bs > 0 else 0.0
            if pct >= 25:
                color = '#dc3545'
            elif pct >= 10:
                color = '#fd7e14'
            else:
                color = '#198754'
            label = f'{pct:.0f}%'
            rows.append(
                '<tr>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF;">{escape(nombre)}</td>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF; text-align:right;">'
                f'Bs.{self._fmt_monto(monto)} ({n})</td>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF; text-align:right;">'
                f'<span style="background-color:{color}; color:#fff; border-radius:10px; '
                f'padding:2px 9px; font-size:0.68rem;">{label}</span></td>'
                '</tr>'
            )
        return (
            '<table style="width:100%; font-size:0.78rem; border-collapse:collapse;">'
            '<tr style="color:#8a8f98; text-align:left;">'
            '<td style="padding-bottom:4px;">Cliente</td>'
            '<td style="padding-bottom:4px; text-align:right;">Monto (# fact.)</td>'
            '<td style="padding-bottom:4px; text-align:right;">Concentraci&#243;n</td></tr>'
            + ''.join(rows) +
            '</table>'
        )

    def _zona_pendiente_html(self, ranking, conteo, vencidas, conciliado_no_decl,
                              total_bs, top_n=10):
        """Ranking de zonas por monto pendiente (Esperado+Vencido) —
        mismo patrón visual que _top_clientes_html. 2 badges de color:
        % del Total (cuánto pesa la zona, concentración clásica) y %
        Vencido (0-10% verde, 10-25% naranja, 25%+ rojo — la señal real
        para decidir dónde enfocar el seguimiento). Columna aparte:
        Monto Conciliado con SENIAT pero No Declarado — crédito fiscal
        ya confirmado por SENIAT, retrasado por falta de comprobante,
        no necesariamente de las mismas retenciones que la columna de
        Pendiente (esa es Eje 1/recepción, esta es Eje 2+3/conciliación
        y declaración — pueden solaparse parcialmente, no son la misma
        población)."""
        if not ranking:
            return (
                '<div class="text-muted" style="font-size:0.8rem; padding:8px 0;">'
                'Sin retenciones pendientes con Zona/Planta asignada</div>'
            )
        filas = ranking[:top_n]
        resto = ranking[top_n:]
        if resto:
            resto_zonas = [z for z, _ in resto]
            filas.append((
                f'Otras ({len(resto)})',
                sum(m for _, m in resto),
            ))
            conteo[f'Otras ({len(resto)})'] = sum(conteo[z] for z in resto_zonas)
            vencidas[f'Otras ({len(resto)})'] = sum(vencidas.get(z, 0) for z in resto_zonas)
            conciliado_no_decl[f'Otras ({len(resto)})'] = sum(
                conciliado_no_decl.get(z, 0.0) for z in resto_zonas)
        rows = []
        for zona, monto in filas:
            n = conteo[zona]
            n_venc = vencidas.get(zona, 0)
            pct_venc = (n_venc / n * 100) if n else 0.0
            pct_total = (monto / total_bs * 100) if total_bs > 0 else 0.0
            if pct_venc >= 25:
                color = '#dc3545'
            elif pct_venc >= 10:
                color = '#fd7e14'
            else:
                color = '#198754'
            monto_conc = conciliado_no_decl.get(zona, 0.0)
            rows.append(
                '<tr>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF;">{escape(zona)}</td>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF; text-align:right;">'
                f'Bs.{self._fmt_monto(monto)} ({n})</td>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF; text-align:right;">'
                f'{pct_total:.0f}%</td>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF; text-align:right;">'
                f'<span style="background-color:{color}; color:#fff; border-radius:10px; '
                f'padding:2px 9px; font-size:0.68rem;">{pct_venc:.0f}% venc.</span></td>'
                f'<td style="padding:5px 4px; border-top:1px solid #EBFFFF; text-align:right; '
                f'color:#dc3545;">Bs.{self._fmt_monto(monto_conc)}</td>'
                '</tr>'
            )
        return (
            '<table style="width:100%; font-size:0.78rem; border-collapse:collapse;">'
            '<tr style="color:#8a8f98; text-align:left;">'
            '<td style="padding-bottom:4px;">Zona/Planta</td>'
            '<td style="padding-bottom:4px; text-align:right;">Monto Pendiente (# ret.)</td>'
            '<td style="padding-bottom:4px; text-align:right;">% del Total</td>'
            '<td style="padding-bottom:4px; text-align:right;">% Vencido</td>'
            '<td style="padding-bottom:4px; text-align:right;" '
            'title="Conciliado con SENIAT pero No Declarado — crédito confirmado, retrasado por falta de comprobante">'
            'Conciliado No Decl.</td></tr>'
            + ''.join(rows) +
            '</table>'
        )

    # ── Compute: Retenciones Pendientes por Zona ──────────────────────────────
    @api.depends()
    def _compute_zona_pendiente(self):
        WH = self.env['ve.wh.iva']
        # estado_declaracion='no_declarado' — mismo fix que
        # _serie_valor_pendiente_total: excluye "declarado_sin_
        # comprobante" (declarado sin el papel físico, state se queda
        # en esperado/vencido a propósito) — ya se usó, no debe seguir
        # contando como pendiente/oportunidad de recuperar.
        pendientes = WH.search([
            ('state', 'in', ('esperado', 'vencido')),
            ('estado_declaracion', '=', 'no_declarado'),
            ('company_id', '=', self.env.company.id),
        ])
        por_zona = {}
        conteo = {}
        vencidas = {}
        for r in pendientes:
            zona = r.zona or 'Sin Zona'
            por_zona[zona] = por_zona.get(zona, 0.0) + r.monto_retenido
            conteo[zona] = conteo.get(zona, 0) + 1
            if r.state == 'vencido':
                vencidas[zona] = vencidas.get(zona, 0) + 1

        # Población aparte (Eje 2+3, no Eje 1): conciliado con SENIAT pero
        # sin declarar todavía — normalmente por falta de comprobante.
        conciliados_sin_declarar = WH.search([
            ('estado_conciliacion', 'in', self._CONCIL_CONCILIADA_ESTADOS),
            ('estado_declaracion', '=', 'no_declarado'),
            ('company_id', '=', self.env.company.id),
        ])
        conciliado_no_decl = {}
        for r in conciliados_sin_declarar:
            zona = r.zona or 'Sin Zona'
            conciliado_no_decl[zona] = conciliado_no_decl.get(zona, 0.0) + r.monto_retenido

        ranking = sorted(por_zona.items(), key=lambda kv: kv[1], reverse=True)
        total_bs = sum(por_zona.values())
        html = self._zona_pendiente_html(
            ranking, conteo, vencidas, conciliado_no_decl, total_bs)
        for rec in self:
            rec.zona_pendiente_html = html

    # ── Compute: Top clientes con comprobantes pendientes ────────────────────
    @api.depends()
    def _compute_top_clientes(self):
        WH = self.env['ve.wh.iva']
        pendientes = WH.search([
            ('estado_cobranza', '=', 'pagado_sin_comprobante'),
            ('company_id', '=', self.env.company.id),
        ])
        por_cliente = {}
        conteo = {}
        for r in pendientes:
            por_cliente[r.partner_id] = por_cliente.get(r.partner_id, 0.0) + r.monto_retenido
            conteo[r.partner_id] = conteo.get(r.partner_id, 0) + 1
        ranking = sorted(por_cliente.items(), key=lambda kv: kv[1], reverse=True)
        total_bs = sum(por_cliente.values())
        top3_bs = sum(m for _, m in ranking[:3])
        pct_top3 = (top3_bs / total_bs * 100) if total_bs > 0 else 0.0
        html = self._top_clientes_html(ranking, conteo, total_bs, top_n=10)
        for rec in self:
            rec.pct_concentracion_top3 = pct_top3
            rec.top_clientes_html = html

    # ── Compute: Lista de Trabajo ─────────────────────────────────────────────
    @api.depends()
    def _compute_lista_trabajo(self):
        # Cualquier retención con al menos una de las 3 banderas necesita_*
        # activas (envio_comp/dif_seniat/rep_seniat) — misma unión exacta que
        # antes calculaba a mano con la tabla de tokens de estado_visual,
        # ahora una sola fuente de verdad compartida con los botones.
        #
        # Bug real encontrado 2026-08-25 (Multiempresa): el 'order' original
        # solo tenía 'fecha_vencimiento_entrega asc', sin desempate. Con 70
        # retenciones vencidas el mismo día (caso real, TEST) y limit=10, un
        # empate tan grande sin criterio de desempate hace que PostgreSQL NO
        # garantice ni el orden ni la selección de esos 10 entre una consulta
        # y otra -- cada escritura (ej. registrar una llamada) podía cambiar
        # cuál retención cae en qué posición la próxima vez que se recalcula
        # este compute, haciendo que el botón de una fila visual terminara
        # escribiendo sobre una retención distinta a la que se veía en
        # pantalla. 'id asc' como desempate hace la selección y el orden
        # 100% estables entre llamadas.
        items = self.env['ve.wh.iva'].search([
            '|', '|',
            ('necesita_envio_comp', '=', True),
            ('necesita_aclarar_dif_seniat', '=', True),
            ('necesita_reportar_seniat', '=', True),
            ('company_id', '=', self.env.company.id),
        ], order='fecha_vencimiento_entrega asc, id asc', limit=10)
        for rec in self:
            rec.lista_trabajo_ids = items

    def action_ver_lista_trabajo_completa(self):
        """Abre la misma Lista de Trabajo pero completa (sin el límite de
        10) — usa una vista de lista DEDICADA (`ve_wh_iva_view_list_
        seguimiento`) con exactamente los mismos campos y botones que el
        widget embebido del Dashboard (Abrir/Llamada/Recordatorios,
        badges de los 3 ejes), no la vista genérica de "Retenciones IVA
        Clientes". Pedido explícito 2026-07-30 (Propuesta 1 de
        PROPUESTA_LISTA_TRABAJO.md, ajustada después para que la vista
        que abre luzca igual a la tarjeta de origen)."""
        list_view = self.env.ref('ve_retencion_iva.ve_wh_iva_view_list_seguimiento')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Retenciones IVA Clientes — No Recibidas',
            'res_model': 've.wh.iva',
            'view_mode': 'list,form',
            'views': [(list_view.id, 'list'), (False, 'form')],
            'search_view_id': self.env.ref('ve_retencion_iva.ve_wh_iva_view_search').id,
            'domain': [('state', 'in', ('esperado', 'vencido')),
                       ('company_id', '=', self.env.company.id)],
        }

    def _aging_bar_html(self, bs_0_15, n_0_15, bs_16_30, n_16_30, bs_31_mas, n_31_mas, h=28):
        """Barra horizontal apilada: qué proporción del riesgo total es
        reciente (0-15) vs. crónica (+30) — de un vistazo, sin tener que
        comparar 3 números sueltos. El tooltip de cada tramo trae cantidad
        de comprobantes + monto + %."""
        total = bs_0_15 + bs_16_30 + bs_31_mas
        if total <= 0:
            return (
                f'<div class="text-center text-muted" style="font-size:0.8rem; padding:{h//2}px 0;">'
                'Sin comprobantes vencidos'
                '</div>'
            )
        w1 = (bs_0_15 / total) * 100
        w2 = (bs_16_30 / total) * 100
        w3 = (bs_31_mas / total) * 100
        segmentos = []
        for bs, n, w, color, label in (
            (bs_0_15, n_0_15, w1, '#ffc107', '0-15 días'),
            (bs_16_30, n_16_30, w2, '#fd7e14', '16-30 días'),
            (bs_31_mas, n_31_mas, w3, '#dc3545', '+30 días'),
        ):
            if w <= 0:
                continue
            segmentos.append(
                f'<div style="width:{w:.1f}%; height:{h}px; background-color:{color};" '
                f'title="{label}: {n} comp. — Bs.{self._fmt_monto(bs)} ({w:.0f}%)"></div>'
            )
        return (
            f'<div class="d-flex rounded overflow-hidden" style="height:{h}px;">'
            + ''.join(segmentos) +
            '</div>'
        )

    def _bar_vertical_html(self, bs, count, color, max_bs, label, h=64):
        """Barra vertical de un solo bucket de Antigüedad — se genera una
        por bucket (no un solo gráfico combinado) para poder envolver cada
        una en su propio <button type="object"> en la vista y que sea
        clickable individualmente. Altura proporcional al monto (Bs),
        mismo criterio de priorización que el resto del bloque. Ancho
        100% (no fijo) para que, sin separación entre los botones que la
        envuelven en la vista, las barras queden pegadas unas a otras."""
        bar_h = int(6 + (h - 6) * (bs / max_bs)) if max_bs > 0 else 6
        return (
            '<div style="text-align:center; width:100%;">'
            f'<div style="font-size:0.72rem; font-weight:700; color:{color};">Bs.{self._fmt_monto(bs)}</div>'
            f'<div style="font-size:0.72rem; font-weight:600; color:#383A4E;">{count} comp.</div>'
            f'<div style="width:100%; height:{h}px; display:flex; align-items:flex-end; margin-top:2px;">'
            f'<div style="width:100%; height:{bar_h}px; background-color:{color};"></div>'
            '</div>'
            f'<div style="font-size:0.7rem; color:#58595B; margin-top:4px;">{label}</div>'
            '</div>'
        )

    def _gauge_html(self, pct, monto_bs, debito_bs, w=150,
                     umbral_ok=10, umbral_alerta=25, tope=50, compact=False):
        """Medidor circular tipo velocímetro (semicírculo, el 'relojito'):
        bandas de color en los umbrales y una aguja apuntando al valor
        actual. tope = valor en % que representa el extremo derecho de la
        escala (valores mayores muestran la aguja al tope, con un '+' en
        el número indicando que está fuera de escala). El tooltip trae el
        monto en Bs y el Débito Fiscal contra el que se compara."""
        r = w / 2 - 10
        cx, cy = w / 2, w / 2 - 2
        h = int(cy + 14)

        def punto(valor, radio):
            ang = math.pi * (1 - max(0.0, min(valor, tope)) / tope)
            return cx + radio * math.cos(ang), cy - radio * math.sin(ang)

        def arco(v_ini, v_fin, color):
            x1, y1 = punto(v_ini, r)
            x2, y2 = punto(v_fin, r)
            return (
                f'<path d="M {x1:.1f} {y1:.1f} A {r:.1f} {r:.1f} 0 0 1 {x2:.1f} {y2:.1f}" '
                f'stroke="{color}" stroke-width="12" fill="none"/>'
            )

        bandas = (
            arco(0, umbral_ok, '#198754')
            + arco(umbral_ok, umbral_alerta, '#fd7e14')
            + arco(umbral_alerta, tope, '#dc3545')
        )

        ax, ay = punto(pct, r - 8)
        aguja = (
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" '
            'stroke="#343a40" stroke-width="3" stroke-linecap="round"/>'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="#343a40"/>'
        )

        if pct < umbral_ok:
            color_txt = '#198754'
        elif pct < umbral_alerta:
            color_txt = '#fd7e14'
        else:
            color_txt = '#dc3545'
        fuera_escala = '+' if pct > tope else ''
        title = (
            f'Bs.{self._fmt_monto(monto_bs)} en riesgo de Bs.{self._fmt_monto(debito_bs)} '
            f'de Débito Fiscal ({pct:.1f}%)'
        )

        # Monto en riesgo, ubicado junto a donde apunta la aguja. El ancla
        # del texto cambia según el lado del semicírculo para que no se
        # salga del contenedor (bajo valor → aguja a la izquierda; alto
        # valor → aguja a la derecha).
        frac = max(0.0, min(pct, tope)) / tope if tope else 0.0
        if frac < 0.35:
            anchor = 'end'
        elif frac > 0.65:
            anchor = 'start'
        else:
            anchor = 'middle'
        lx, ly = punto(pct, r + 16)
        etiqueta_riesgo = (
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="9" font-weight="600" '
            f'text-anchor="{anchor}" fill="{color_txt}">'
            f'Bs.{self._fmt_monto(monto_bs)}</text>'
        )

        gauge_svg = (
            '<div style="text-align:center;">'
            f'<svg viewBox="0 0 {w} {h}" style="width:{w}px; height:{h}px; overflow:visible;">'
            f'{bandas}{aguja}{etiqueta_riesgo}'
            '</svg>'
            f'<div class="fw-bold small" style="color:{color_txt}; margin-top:-4px;">'
            f'{pct:.1f}{fuera_escala}%</div>'
            '</div>'
        )
        if compact:
            # Débito Fiscal debajo (no al lado) — evita que su texto se
            # superponga con la etiqueta de monto junto a la aguja cuando
            # el gauge vive en una columna angosta (p.ej. 1/3 de fila).
            return (
                f'<div style="text-align:center;" title="{title}">'
                f'{gauge_svg}'
                '<div style="font-size:0.68rem;" class="text-muted mt-1">Débito Fiscal</div>'
                f'<div style="font-size:0.7rem;" class="fw-bold">Bs.{self._fmt_monto(debito_bs)}</div>'
                '</div>'
            )
        return (
            '<div class="d-flex align-items-center justify-content-center" '
            f'style="gap:10px;" title="{title}">'
            f'{gauge_svg}'
            '<div class="text-start" style="font-size:0.72rem; line-height:1.3;">'
            '<div class="text-muted">Débito Fiscal</div>'
            f'<div class="fw-bold">Bs.{self._fmt_monto(debito_bs)}</div>'
            '</div>'
            '</div>'
        )

    # ── Compute: Antigüedad de comprobantes vencidos (aging) ─────────────────
    @api.depends()
    def _compute_aging(self):
        hoy = fields.Date.today()
        year_start, year_end = self._get_rango_ytd()
        pendientes = self.env['ve.wh.iva'].search([
            ('state', 'in', ('esperado', 'vencido')),
            ('fecha_vencimiento_entrega', '<=', hoy),
            ('company_id', '=', self.env.company.id),
        ])
        b1 = pendientes.filtered(lambda r: (hoy - r.fecha_vencimiento_entrega).days <= 15)
        b2 = pendientes.filtered(
            lambda r: 15 < (hoy - r.fecha_vencimiento_entrega).days <= 30)
        b3 = pendientes.filtered(lambda r: (hoy - r.fecha_vencimiento_entrega).days > 30)

        bs_0_15 = sum(b1.mapped('monto_retenido'))
        bs_16_30 = sum(b2.mapped('monto_retenido'))
        bs_31_mas = sum(b3.mapped('monto_retenido'))
        aging_total = bs_0_15 + bs_16_30 + bs_31_mas
        bar_html = self._aging_bar_html(
            bs_0_15, len(b1), bs_16_30, len(b2), bs_31_mas, len(b3))
        max_bs = max(bs_0_15, bs_16_30, bs_31_mas, 1.0)
        bar_0_15_html = self._bar_vertical_html(bs_0_15, len(b1), '#ffc107', max_bs, '0-15 días')
        bar_16_30_html = self._bar_vertical_html(bs_16_30, len(b2), '#fd7e14', max_bs, '16-30 días')
        bar_31_mas_html = self._bar_vertical_html(bs_31_mas, len(b3), '#dc3545', max_bs, '+30 días')

        for rec in self:
            rec.aging_0_15_count = len(b1)
            rec.aging_0_15_bs = bs_0_15
            rec.aging_16_30_count = len(b2)
            rec.aging_16_30_bs = bs_16_30
            rec.aging_31_mas_count = len(b3)
            rec.aging_31_mas_bs = bs_31_mas
            rec.aging_total_count = len(pendientes)
            rec.aging_total_bs = aging_total
            rec.aging_bar_0_15_html = bar_0_15_html
            rec.aging_bar_16_30_html = bar_16_30_html
            rec.aging_bar_31_mas_html = bar_31_mas_html

            periodo = rec._get_periodo_activo()
            # Por fecha_vencimiento_entrega dentro de la ventana del
            # período (no por conciliacion_id) — mismo criterio que
            # total_esperados/sin_p: el plazo de un comprobante siempre
            # vence en la quincena SIGUIENTE a la retención, así que uno
            # del período activo nunca puede estar vencido mientras ese
            # período sigue corriendo.
            #
            # Bug real encontrado 2026-08-27 (Multiempresa): faltaba el
            # límite SUPERIOR (periodo.fecha_fin) -- con solo la cota
            # inferior, cuando _get_periodo_activo() cae al fallback (no
            # hay período que contenga HOY, se usa el último no
            # declarado, que puede tener fecha_inicio de meses atrás),
            # esto sumaba TODO lo vencido desde ese inicio hasta hoy --
            # varios meses/todo el año, no solo la ventana del período.
            aging_periodo = sum(
                pendientes.filtered(
                    lambda r: periodo.fecha_inicio <= r.fecha_vencimiento_entrega <= periodo.fecha_fin)
                .mapped('monto_retenido')
            ) if periodo else 0.0
            rec.aging_periodo_bs = aging_periodo

            debito_periodo = (
                periodo.declaracion_iva_id.campo_49
                if periodo and periodo.declaracion_iva_id else 0.0
            )
            rec.aging_pct_debito_periodo = (
                aging_periodo / debito_periodo * 100) if debito_periodo > 0 else 0.0

            debito_ytd = rec.debito_fiscal_cascade
            # Mismo alcance que sin_ytd en _compute_liquidez (solo año
            # calendario en curso) para que el velocímetro YTD y el "%
            # en riesgo" de Cascada de Liquidez midan exactamente lo
            # mismo y nunca se descuadren entre sí.
            # Igual que domain_ytd en _compute_liquidez: un pendiente SIN
            # conciliacion_id (aún no reconciliado a ningún período) debe
            # contar en el YTD también, no solo los que ya tienen período
            # asignado dentro del año.
            aging_ytd = sum(
                pendientes.filtered(
                    lambda r: not r.conciliacion_id
                    or year_start <= r.conciliacion_id.fecha_inicio <= year_end
                ).mapped('monto_retenido')
            )
            rec.aging_pct_debito_ytd = (
                aging_ytd / debito_ytd * 100) if debito_ytd > 0 else 0.0

            rec.aging_meter_periodo_html = self._gauge_html(
                rec.aging_pct_debito_periodo, aging_periodo, debito_periodo,
                w=110, compact=True)
            rec.aging_meter_ytd_html = self._gauge_html(
                rec.aging_pct_debito_ytd, aging_ytd, debito_ytd,
                w=110, compact=True)
            rec.aging_bar_html = bar_html

    # ── Compute: KPI 1 — Margen Crédito / Débito ─────────────────────────────
    @api.depends()
    def _compute_margen_cd(self):
        year_start, year_end = self._get_rango_ytd()
        for rec in self:
            # Período más reciente con datos
            ultimo = self.env['ve.conciliacion.periodo'].search([
                ('estado', 'in', ['borrador', 'revision', 'aprobado', 'declarado']),
                ('company_id', '=', self.env.company.id),
            ], order='fecha_fin desc', limit=1)
            if ultimo:
                decl = ultimo.declaracion_iva_id
                rec.periodo_ref_id = ultimo.id
                rec.periodo_ref_name = ultimo.periodo_retencion or ultimo.name or '—'
                rec.credito_fiscal_periodo = decl.campo_39 if decl else 0.0
                rec.campo_49_periodo = decl.campo_49 if decl else 0.0
                c39_p = decl.campo_39 if decl else 0.0
                c49_p = decl.campo_49 if decl else 0.0
                rec.margen_cd_periodo = (c39_p / c49_p * 100) if c49_p > 0 else 0.0
            else:
                rec.periodo_ref_id = 0
                rec.periodo_ref_name = 'Sin datos'
                rec.credito_fiscal_periodo = 0.0
                rec.campo_49_periodo = 0.0
                rec.margen_cd_periodo = 0.0

            # YTD: suma de todos los períodos del año via declaracion_iva_id
            periodos_anio = self.env['ve.conciliacion.periodo'].search([
                ('estado', 'in', ['borrador', 'revision', 'aprobado', 'declarado']),
                ('fecha_fin', '>=', year_start),
                ('fecha_fin', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ])
            c39 = sum(p.declaracion_iva_id.campo_39 for p in periodos_anio if p.declaracion_iva_id)
            c49 = sum(p.declaracion_iva_id.campo_49 for p in periodos_anio if p.declaracion_iva_id)
            rec.credito_fiscal_ytd = c39
            rec.campo_49_ytd = c49
            rec.margen_cd_ytd = (c39 / c49 * 100) if c49 > 0 else 0.0

    # ── Compute: riesgo de declaración (crédito declarado sin comprobante) ───
    @api.depends()
    def _compute_riesgo_declaracion(self):
        WH = self.env['ve.wh.iva']
        year_start, year_end = self._get_rango_ytd()
        for rec in self:
            periodo = rec._get_periodo_activo()
            if periodo:
                activos_p = WH.search([
                    ('conciliacion_id', '=', periodo.id),
                    ('state', '!=', 'anulado'),
                    ('incluir_declaracion', '=', True),
                ])
                total_p = sum(activos_p.mapped('monto_c66'))
                sin_p = sum(activos_p.filtered(
                    lambda r: r.state in ('esperado', 'vencido')).mapped('monto_c66'))
                rec.c66_total_periodo_bs = total_p
                rec.c66_sin_confirmar_periodo_bs = sin_p
                rec.pct_c66_sin_confirmar_periodo = (sin_p / total_p * 100) if total_p > 0 else 0.0
            else:
                rec.c66_total_periodo_bs = 0.0
                rec.c66_sin_confirmar_periodo_bs = 0.0
                rec.pct_c66_sin_confirmar_periodo = 0.0

            activos_ytd = WH.search([
                '|',
                    ('conciliacion_id', '=', False),
                    '&', ('conciliacion_id.fecha_inicio', '>=', year_start),
                         ('conciliacion_id.fecha_inicio', '<=', year_end),
                ('state', '!=', 'anulado'),
                ('incluir_declaracion', '=', True),
            ])
            total_ytd = sum(activos_ytd.mapped('monto_c66'))
            sin_ytd = sum(activos_ytd.filtered(
                lambda r: r.state in ('esperado', 'vencido')).mapped('monto_c66'))
            rec.c66_total_ytd_bs = total_ytd
            rec.c66_sin_confirmar_ytd_bs = sin_ytd
            rec.pct_c66_sin_confirmar_ytd = (sin_ytd / total_ytd * 100) if total_ytd > 0 else 0.0

    # ── Compute: Declarado (C.66) vs. SENIAT ──────────────────────────────────
    # Replanteado 2026-08-27, pedido explícito: la versión anterior
    # ("Posición Neta SENIAT") era un espejo del Campo 90 (Débito − Créditos
    # de COMPRAS − Retenciones declaradas) -- 100% autoreferencial a la
    # propia declaración de Odoo, sin cruzar nada contra el SENIAT real, pese
    # al nombre. Caso real que motivó el cambio (Vencement): lo que de
    # verdad quieren monitorear es CUÁNTO declararon en su Campo 66 (C.66 —
    # Retenciones del Período) CONTRA cuánto sus clientes (agentes de
    # retención) efectivamente reportaron al portal SENIAT por ellos
    # (ve.conciliacion.periodo.total_seniat, ya usado en BDS de IOC/TAC/BDS
    # pero ahí comparado contra "Esperadas", no contra lo REALMENTE
    # declarado). Positivo = declarado MÁS de lo que el portal respalda
    # (riesgo: crédito sin soporte SENIAT visible). Negativo = el portal
    # tiene MÁS retenciones reportadas de las que se llegaron a declarar
    # (oportunidad: crédito real sin aprovechar todavía).
    @api.depends()
    def _compute_posicion_neta(self):
        year_start, year_end = self._get_rango_ytd()
        for rec in self:
            # Período: el mismo "último período con datos" que usa Margen C/D.
            ultimo = self.env['ve.conciliacion.periodo'].search([
                ('estado', 'in', ['borrador', 'revision', 'aprobado', 'declarado']),
                ('company_id', '=', self.env.company.id),
            ], order='fecha_fin desc', limit=1)
            declarado_p = ultimo.declaracion_iva_id.campo_66 if ultimo and ultimo.declaracion_iva_id else 0.0
            seniat_p = ultimo.total_seniat if ultimo else 0.0
            p = declarado_p - seniat_p

            periodos_anio = self.env['ve.conciliacion.periodo'].search([
                ('estado', 'in', ['borrador', 'revision', 'aprobado', 'declarado']),
                ('fecha_fin', '>=', year_start),
                ('fecha_fin', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ])
            declarado_y = sum(
                per.declaracion_iva_id.campo_66
                for per in periodos_anio if per.declaracion_iva_id
            )
            seniat_y = sum(periodos_anio.mapped('total_seniat'))
            y = declarado_y - seniat_y

            rec.posicion_neta_periodo_bs = abs(p)
            rec.posicion_neta_periodo_label = 'Declarado &gt; SENIAT' if p >= 0 else 'SENIAT &gt; Declarado'
            rec.posicion_neta_ytd_bs = abs(y)
            rec.posicion_neta_ytd_label = 'Declarado &gt; SENIAT' if y >= 0 else 'SENIAT &gt; Declarado'

    # ── Compute: Brecha (pendiente de confirmar, complementa Posición Neta) ──
    # Misma fuente que "Retenciones s/Comprobante" en la Cascada de Liquidez
    # (retenido_sin_periodo / retenido_sin_periodo_count / pct_en_riesgo_periodo)
    # — un solo cálculo, para que la ficha KPI y la fila de la Cascada nunca
    # se desincronicen.
    @api.depends()
    def _compute_brecha(self):
        for rec in self:
            rec.brecha_pendiente_bs = rec.retenido_sin_periodo
            rec.brecha_pendiente_count = rec.retenido_sin_periodo_count
            rec.brecha_pendiente_pct = rec.pct_en_riesgo_periodo
            rec.brecha_gauge_html = rec._donut_html(rec.pct_en_riesgo_periodo)

    # ── Compute: Excedente de Crédito Fiscal Acumulado ────────────────────────
    @api.depends()
    def _compute_excedente(self):
        Periodo = self.env['ve.conciliacion.periodo']
        for rec in self:
            periodo = rec._get_periodo_activo()
            decl = periodo.declaracion_iva_id if periodo else False
            rec.excedente_trasladable_bs = decl.campo_60 if decl else 0.0
            rec.excedente_periodo_ref_name = periodo.periodo_retencion if periodo else '—'
            if periodo and periodo.fecha_inicio:
                anterior = Periodo.search([
                    ('fecha_fin', '<', periodo.fecha_inicio),
                    ('company_id', '=', self.env.company.id),
                ], order='fecha_fin desc', limit=1)
            else:
                anterior = Periodo.browse()
            prev_val = (
                anterior.declaracion_iva_id.campo_60
                if anterior and anterior.declaracion_iva_id else 0.0
            )
            rec.excedente_tendencia_pct = (
                (rec.excedente_trasladable_bs - prev_val) / prev_val * 100
            ) if prev_val else 0.0

    # Estados "conciliada" — mismo criterio que ve_conciliacion.py::
    # _compute_totales (rec.conciliadas), duplicado acá para no depender de
    # cruzar módulos al armar los buckets de arbitrarios (YTD abarca varios
    # períodos a la vez, no uno solo).
    _CONCIL_CONCILIADA_ESTADOS = (
        'conciliada', 'conciliada_norec', 'listo_declarar', 'declarado', 'aprobado_declarar')

    # Estados para la barra "Conciliado" del gráfico de 6 series (mensual y
    # Ene-Jun, ver _serie_valor_conciliado) -- a propósito un set MÁS ANCHO
    # que _CONCIL_CONCILIADA_ESTADOS: acá SÍ se incluye 'diferencia'
    # (pedido explícito 2026-08-22, la usuaria notó que ese gráfico de 6
    # barras no tiene una barra aparte para "Diferencia" como sí tiene la
    # dona de Salud de Conciliación -- por eso una retención con diferencia
    # de monto no debe desaparecer del todo, tiene que contar como
    # conciliada igual). NO usar este set más ancho en _calc_concil_buckets
    # (la dona) -- ahí sí hay una porción separada para "Diferencia de
    # monto"; agregar 'diferencia' también a _CONCIL_CONCILIADA_ESTADOS
    # duplicaría esas filas en 2 porciones de la misma dona.
    _SERIE_CONCILIADO_ESTADOS = _CONCIL_CONCILIADA_ESTADOS + ('diferencia',)

    def _calc_concil_buckets(self, wh_activos, seniat_recs):
        """Cantidad y monto de las 6 categorías de conciliación, para
        cualquier alcance (un período o varios — YTD). wh_activos/
        seniat_recs ya deben venir filtrados a state != 'anulado' /
        compañía. Compartido entre Período Activo y YTD (2026-07-31) para
        no calcular la Salud de Conciliación dos veces con criterios
        distintos."""
        conciliadas = wh_activos.filtered(
            lambda r: r.estado_conciliacion in self._CONCIL_CONCILIADA_ESTADOS)
        solo_odoo = wh_activos.filtered(lambda r: r.estado_conciliacion == 'solo_odoo')
        con_diferencia = wh_activos.filtered(lambda r: r.estado_conciliacion == 'diferencia')
        # Comprobantes que todavía no pasaron por el proceso de conciliación
        # SENIAT (nadie subió el XLSX/corrió el RPA todavía) — sin este
        # bucket, un período con comprobantes reales pero sin conciliar se
        # veía igual que uno sin ningún comprobante.
        sin_conciliar = wh_activos.filtered(lambda r: r.estado_conciliacion == 'pendiente')
        solo_seniat = seniat_recs.filtered(lambda s: s.estado == 'sin_match')
        # Sin Procesar: registros SENIAT que todavía están en 'cargado' --
        # nadie corrió "Conciliar SENIAT" sobre ellos. Bucket nuevo, pedido
        # explícito 2026-08-10 tras encontrar que en Cementos el 92% del
        # monto SENIAT del año (Bs. 3.078M de Bs. 3.337M) seguía en
        # 'cargado' y no caía en NINGUNA de las 5 categorías anteriores --
        # Acumulado YTD se veía completo (100%) mostrando solo el 8% real
        # del trabajo de conciliación. Separado de "Sin Conciliar" a
        # propósito: ese es del lado Odoo (wh_iva sin procesar contra
        # SENIAT), este es del lado SENIAT (nunca ni se intentó conciliar).
        sin_procesar = seniat_recs.filtered(lambda s: s.estado == 'cargado')
        return [
            ('Conciliada', len(conciliadas),
             sum(conciliadas.mapped('monto_retenido')), '#198754'),
            ('Solo SmartIVA', len(solo_odoo),
             sum(solo_odoo.mapped('monto_retenido')), '#dc3545'),
            ('Solo SENIAT', len(solo_seniat),
             sum(solo_seniat.mapped('monto_retenido')), '#fd7e14'),
            ('Diferencia de monto', len(con_diferencia),
             sum(con_diferencia.mapped('monto_retenido')), '#8a8f98'),
            ('Sin Conciliar', len(sin_conciliar),
             sum(sin_conciliar.mapped('monto_retenido')), '#adb5bd'),
            ('Sin Procesar (SENIAT)', len(sin_procesar),
             sum(sin_procesar.mapped('monto_retenido')), '#6f42c1'),
        ]

    # ── Compute: Conciliación de Datos vs. SENIAT (4 vías) ────────────────────
    @api.depends()
    def _compute_salud_conciliacion(self):
        Periodo = self.env['ve.conciliacion.periodo']
        year_start, year_end = self._get_rango_ytd()
        for rec in self:
            periodo = rec._get_periodo_activo()
            if periodo:
                activos = periodo.wh_iva_ids.filtered(lambda r: r.state != 'anulado')
                buckets = rec._calc_concil_buckets(activos, periodo.seniat_ids)
            else:
                buckets = []
            (rec.concil_conciliadas_count, rec.concil_solo_odoo_count,
             rec.concil_solo_seniat_count, rec.concil_con_diferencia_count,
             rec.concil_sin_conciliar_count) = (
                (buckets[0][1], buckets[1][1], buckets[2][1], buckets[3][1], buckets[4][1])
                if buckets else (0, 0, 0, 0, 0)
            )
            rec.concil_bar_html = rec._conteo_donut_html(buckets)

            # YTD — todos los períodos de la compañía cuya quincena cae
            # dentro del año calendario actual (mismo rango que ya usan
            # Posición Neta/Tasa Efectiva de este dashboard, ver
            # _get_rango_ytd). Pedido explícito 2026-07-31: la versión
            # anterior solo existía para el período activo.
            periodos_ytd = Periodo.search([
                ('fecha_fin', '>=', year_start), ('fecha_fin', '<=', year_end),
                ('company_id', '=', rec.env.company.id),
            ])
            activos_ytd = periodos_ytd.mapped('wh_iva_ids').filtered(
                lambda r: r.state != 'anulado')
            seniat_ytd = periodos_ytd.mapped('seniat_ids')
            buckets_ytd = rec._calc_concil_buckets(activos_ytd, seniat_ytd)
            rec.concil_bar_html_ytd = rec._conteo_donut_html(buckets_ytd)

    # ── Compute: KPI 2 — Tasa Efectiva ────────────────────────────────────────
    @api.depends()
    def _compute_tasa_ef(self):
        today = fields.Date.today()
        year_start, year_end = self._get_rango_ytd()
        for rec in self:
            rec.tasa_anio = today.year

            # Período activo
            periodo = rec._get_periodo_activo()
            if periodo:
                # Confirmados: tienen comprobante físico → numerador
                conf_p = self.env['ve.wh.iva'].search([
                    ('conciliacion_id', '=', periodo.id),
                    ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
                ])
                # Denominador: todos los válidos del período, EXCLUYENDO los
                # "esperado" cuyo plazo legal aún no vence — antes de esa
                # fecha el cliente no está en incumplimiento, incluirlos
                # solo genera ruido de calendario (baja/sube la tasa según
                # cuántas facturas se emitieron recién, no según gestión).
                todos_p = self.env['ve.wh.iva'].search([
                    ('conciliacion_id', '=', periodo.id),
                    ('state', '!=', 'anulado'),
                    '|', ('state', '!=', 'esperado'),
                         ('fecha_vencimiento_entrega', '<=', today),
                ])
                iva_p = sum(r.monto_iva + r.monto_iva_red for r in todos_p)
                ret_p = sum(conf_p.mapped('monto_retenido'))
                rec.tasa_ef_periodo = (ret_p / iva_p * 100) if iva_p > 0 else 0.0
                rec.tasa_ef_retenido_periodo = ret_p
                rec.tasa_ef_causado_periodo = iva_p
            else:
                rec.tasa_ef_periodo = 0.0
                rec.tasa_ef_retenido_periodo = 0.0
                rec.tasa_ef_causado_periodo = 0.0

            # YTD
            conf_y = self.env['ve.wh.iva'].search([
                ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
                ('conciliacion_id.fecha_inicio', '>=', year_start),
                ('conciliacion_id.fecha_inicio', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ])
            todos_y = self.env['ve.wh.iva'].search([
                ('state', '!=', 'anulado'),
                '|', ('state', '!=', 'esperado'),
                     ('fecha_vencimiento_entrega', '<=', today),
                ('conciliacion_id.fecha_inicio', '>=', year_start),
                ('conciliacion_id.fecha_inicio', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ])
            iva_y = sum(r.monto_iva + r.monto_iva_red for r in todos_y)
            ret_y = sum(conf_y.mapped('monto_retenido'))
            rec.tasa_ef_ytd = (ret_y / iva_y * 100) if iva_y > 0 else 0.0
            rec.tasa_ef_retenido_ytd = ret_y
            rec.tasa_ef_causado_ytd = iva_y

    # ── Compute: KPI 3 — Cumplimiento SPE ────────────────────────────────────
    @api.depends()
    def _compute_cumplimiento(self):
        today = fields.Date.today()
        for rec in self:
            pct_4q, ok_4q, tot_4q = rec._cumplimiento_en_rango(today - timedelta(days=60))
            pct_12m, ok_12m, tot_12m = rec._cumplimiento_en_rango(
                today - relativedelta(months=12))
            rec.pct_cumpl_4q = pct_4q
            rec.periodos_en_plazo_4q = ok_4q
            rec.periodos_eval_4q = tot_4q
            rec.pct_cumpl_12m = pct_12m
            rec.periodos_en_plazo_12m = ok_12m
            rec.periodos_eval_12m = tot_12m

    # ── Series históricas para los sparklines KPI 1-3 (últimas 6 quincenas) ──
    def _serie_ultimos_periodos(self, n=N_PUNTOS_SERIE):
        periodos = self.env['ve.conciliacion.periodo'].search(
            [
                ('estado', 'in', ['borrador', 'revision', 'aprobado', 'declarado']),
                ('company_id', '=', self.env.company.id),
            ],
            order='fecha_fin desc', limit=n,
        )
        return periodos[::-1]

    def _serie_valor_margen_cd(self, periodo):
        decl = periodo.declaracion_iva_id
        c39 = decl.campo_39 if decl else 0.0
        c49 = decl.campo_49 if decl else 0.0
        return (c39 / c49 * 100) if c49 > 0 else 0.0

    def _serie_valor_tasa_efectiva(self, periodo):
        WH = self.env['ve.wh.iva']
        confirmados = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
        ])
        # Mismo criterio que _compute_tasa_ef: excluir "esperado" cuyo plazo
        # legal aún no vence (solo relevante para el período activo, ya que
        # en períodos cerrados todos los plazos ya pasaron hace tiempo).
        todos = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('state', '!=', 'anulado'),
            '|', ('state', '!=', 'esperado'),
                 ('fecha_vencimiento_entrega', '<=', fields.Date.today()),
        ])
        iva = sum(r.monto_iva + r.monto_iva_red for r in todos)
        return (sum(confirmados.mapped('monto_retenido')) / iva * 100) if iva > 0 else 0.0

    def _serie_valor_cumplimiento(self, periodo):
        decl = periodo.declaracion_iva_id
        a_tiempo = (
            periodo.estado == 'declarado'
            and decl and decl.fecha_declaracion
            and periodo.fecha_fin
            and decl.fecha_declaracion.date() <= periodo.fecha_fin + timedelta(days=7)
        )
        return 100.0 if a_tiempo else 0.0

    def _serie_label_corto(self, periodo_retencion):
        """'2026-05 2Q' → '05/2Q' — compacto para caber bajo 6 puntos en la tarjeta."""
        if not periodo_retencion:
            return ''
        m = re.match(r'^\d{4}-(\d{2}) (\dQ)$', periodo_retencion)
        return f'{m.group(1)}/{m.group(2)}' if m else periodo_retencion

    def _serie_a_svg(self, valores, w, h, pad_x, pad_y):
        """Convierte una lista de valores en la geometría SVG de un mini
        gráfico de tendencia (polyline + área + punto final).

        Escala entre el MÍNIMO y el MÁXIMO de la serie (no desde cero):
        para una métrica que se mueve en un rango angosto lejos de cero
        (ej. un margen que va de 17% a 23%), escalar desde cero comprime
        toda la variación real en una franja mínima del gráfico y la
        línea se ve plana salvo por el último punto. Escalar por rango
        muestra la tendencia real de la serie."""
        n = len(valores)
        baseline_y = h - pad_y
        validos = [v for v in valores if v is not None]
        minimo = min(validos, default=0.0)
        maximo = max(validos, default=0.0)
        rango = (maximo - minimo) or 1.0
        xs, ys = [], []
        for i, valor in enumerate(valores):
            v = valor if valor is not None else minimo
            pct = (v - minimo) / rango
            x = pad_x + i * (w - 2 * pad_x) / (n - 1) if n > 1 else w / 2
            y = baseline_y - pct * (h - 2 * pad_y)
            xs.append(x)
            ys.append(y)
        svg_points = ' '.join(f'{x:.1f},{y:.1f}' for x, y in zip(xs, ys))
        svg_area = (
            svg_points + f' {xs[-1]:.1f},{baseline_y:.1f} {xs[0]:.1f},{baseline_y:.1f}'
            if xs else ''
        )
        return {
            'svg_points': svg_points,
            'svg_area': svg_area,
            'last_cx': round(xs[-1]) if xs else 0,
            'last_cy': round(ys[-1]) if ys else 0,
        }

    # ── Compute: sparklines de las tarjetas KPI 1-3 ──────────────────────────
    def _sparkline_html(self, geo, color, w, h, labels, kpi_name, valores,
                         periodos_full, unidad='%', hex_color=None):
        """Arma el <svg> + la leyenda del eje X como un solo bloque HTML.
        Cada etiqueta del eje X lleva un title con el nombre del KPI, el
        período completo y el valor, para que el cursor muestre el detalle
        del punto sin depender del color/posición del punto en el SVG.
        Todo el contenido viene de valores numéricos calculados en Python
        (round/format con punto, nunca coma de locale) o de labels de período
        con formato interno controlado — sin datos de usuario, seguro con
        sanitize=False. hex_color: si se pasa, usa ese color exacto (style)
        en vez de la clase Bootstrap text-{color} — para tarjetas con
        paleta propia (p.ej. Tasa Efectiva, más sobria que el verde
        Bootstrap por defecto)."""
        class_attr = '' if hex_color else f' class="text-{color}"'
        style_attr = f' style="width:100%; height:{h}px; display:block; color:{hex_color};"' if hex_color \
            else f' style="width:100%; height:{h}px; display:block;"'
        svg = (
            f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none"'
            f'{style_attr}{class_attr}>'
            f'<polygon points="{geo["svg_area"]}" fill="currentColor" fill-opacity="0.12" stroke="none"/>'
            f'<polyline points="{geo["svg_points"]}" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{geo["last_cx"]}" cy="{geo["last_cy"]}" r="3" fill="currentColor" '
            f'stroke="white" stroke-width="1"/>'
            f'</svg>'
        )
        spans = []
        for lbl, val, per in zip(labels, valores, periodos_full):
            if val is None or not per:
                spans.append(f'<span>{lbl}</span>')
            else:
                title = f'{kpi_name} — {per}: {val}{unidad}'
                spans.append(f'<span title="{title}" style="cursor:default;">{lbl}</span>')
        labels_row = (
            '<div class="d-flex justify-content-between px-1" '
            'style="font-size:0.62rem; color:#8a8f98;">'
            + ''.join(spans)
            + '</div>'
        )
        return f'<div>{svg}{labels_row}</div>'

    @api.depends()
    def _compute_sparklines(self):
        periodos = self._serie_ultimos_periodos()
        faltan = N_PUNTOS_SERIE - len(periodos)
        dims = {'w': 260, 'h': 36, 'pad_x': 6, 'pad_y': 4}

        def valores_de(fn):
            return [None] * faltan + [round(fn(p), 1) for p in periodos]

        labels = [''] * faltan + [
            self._serie_label_corto(p.periodo_retencion or p.name or '') for p in periodos
        ]
        periodos_full = [''] * faltan + [
            (p.periodo_retencion or p.name or '') for p in periodos
        ]

        valores_margen = valores_de(self._serie_valor_margen_cd)
        valores_tasa = valores_de(self._serie_valor_tasa_efectiva)
        valores_cumpl = valores_de(self._serie_valor_cumplimiento)

        geo_margen = self._serie_a_svg(valores_margen, **dims)
        geo_tasa = self._serie_a_svg(valores_tasa, **dims)
        geo_cumpl = self._serie_a_svg(valores_cumpl, **dims)

        html_margen = self._sparkline_html(
            geo_margen, 'primary', dims['w'], dims['h'], labels,
            'Margen C/D', valores_margen, periodos_full)
        html_tasa = self._sparkline_html(
            geo_tasa, 'success', dims['w'], dims['h'], labels,
            'Recuperación por Retención', valores_tasa, periodos_full,
            hex_color='#5b9a55')
        html_cumpl = self._sparkline_html(
            geo_cumpl, 'warning', dims['w'], dims['h'], labels,
            'Puntualidad Fiscal', valores_cumpl, periodos_full)

        for rec in self:
            rec.margen_svg_html = html_margen
            rec.tasa_svg_html = html_tasa
            rec.cumpl_svg_html = html_cumpl

    def _serie_valor_pendiente_total(self, periodo):
        """Monto retenido TOTAL Pendiente por Recibir (Eje 1 — el
        comprobante físico todavía no llegó: Esperado o Vencido), para
        ESE período. Excluye 'declarado_sin_comprobante' — existe un
        camino real (ve_declaracion_iva.py::_marcar_declarado, "sin_
        comprobante") donde una retención se declara SIN el papel
        físico: `state` se queda en Esperado/Vencido a propósito (el
        papel de verdad no llegó), pero `estado_declaracion` ya pasa a
        'declarado' — ya se usó, no debe seguir contando como pendiente
        real ni como oportunidad de recuperar (bug real detectado
        2026-08-01, la usuaria preguntó explícitamente por esto)."""
        WH = self.env['ve.wh.iva']
        recs = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('state', 'in', ('esperado', 'vencido')),
            ('estado_declaracion', '=', 'no_declarado'),
        ])
        return sum(recs.mapped('monto_retenido'))

    def _serie_valor_conciliado(self, periodo):
        """Monto TOTAL ya conciliado con SENIAT en ESE período, CON o SIN
        diferencia de monto, con o sin comprobante físico recibido --
        usa _SERIE_CONCILIADO_ESTADOS (más ancho que
        _CONCIL_CONCILIADA_ESTADOS de la dona de Salud de Conciliación:
        acá sí incluye 'diferencia'). Ampliado 2026-08-22 (pedido
        explícito): antes una retención con diferencia de monto contra
        SENIAT desaparecía de este gráfico -- no sumaba en "Conciliado" ni
        tenía una barra propia (a diferencia de la dona, que sí separa
        "Diferencia de monto" en su propia porción). Ampliado antes,
        2026-08-10, por el mismo motivo de fondo (no perder registros del
        gráfico): la versión original solo contaba lo
        pendiente-por-recibir-pero-ya-conciliado (subconjunto angosto),
        que en Cementos daba 0 porque ahí todo lo conciliado ya tiene
        comprobante confirmado (state='confirmado', no esperado/vencido)."""
        WH = self.env['ve.wh.iva']
        recs = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('state', '!=', 'anulado'),
            ('estado_conciliacion', 'in', self._SERIE_CONCILIADO_ESTADOS),
        ])
        return sum(recs.mapped('monto_retenido'))

    def _serie_cantidad_conciliado(self, periodo):
        """Cantidad de comprobantes conciliados en ESE período -- mismo
        universo/criterio que _serie_valor_conciliado (ver ahí), para el
        tooltip "Cantidad/Monto" de RESUMEN YTD (pedido explícito
        2026-08-22)."""
        WH = self.env['ve.wh.iva']
        return WH.search_count([
            ('conciliacion_id', '=', periodo.id),
            ('state', '!=', 'anulado'),
            ('estado_conciliacion', 'in', self._SERIE_CONCILIADO_ESTADOS),
        ])

    def _serie_valor_recibido(self, periodo):
        """Monto ya RECIBIDO con comprobante físico, para ESE período --
        usa ve.wh.iva._RECIBIDO_ESTADOS (recibido/recibido_dif/confirmado/
        confirmado_dif), no solo confirmado/confirmado_dif -- corregido
        2026-08-14, pedido explícito de la usuaria: la versión anterior
        excluía comprobantes que ya llegaron pero aún no pasaron por
        "Confirmar", subestimando el KPI en Bs. 536,786,237 (verificado
        contra Cementos). También usa monto_recibido (el monto REAL del
        comprobante, ver ve_wh_iva.py::_compute_monto_recibido) en vez de
        monto_retenido (el esperado legal) -- para que "Recibido" refleje
        lo que el comprobante dice, no lo que se esperaba."""
        WH = self.env['ve.wh.iva']
        recs = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('estado_recepcion', 'in', WH._RECIBIDO_ESTADOS),
        ])
        return sum(recs.mapped('monto_recibido'))

    def _serie_cantidad_recibido(self, periodo):
        """Cantidad de comprobantes RECIBIDOS en ESE período -- mismo
        criterio/universo que _serie_valor_recibido (ver ahí), para la
        tarjeta IOC (pedido explícito 2026-08-22: mostrar cantidad/monto,
        no solo el %)."""
        WH = self.env['ve.wh.iva']
        return WH.search_count([
            ('conciliacion_id', '=', periodo.id),
            ('estado_recepcion', 'in', WH._RECIBIDO_ESTADOS),
        ])

    def _serie_cantidad_estimado(self, periodo):
        """Cantidad de comprobantes activos (state != anulado) en ESE
        período -- mismo universo/criterio que _serie_valor_estimado (ver
        ahí), para poder calcular "cantidad que falta" en las tarjetas
        IOC/BDS (pedido explícito 2026-08-22)."""
        WH = self.env['ve.wh.iva']
        return WH.search_count([
            ('conciliacion_id', '=', periodo.id),
            ('state', '!=', 'anulado'),
        ])

    def _serie_valor_estimado(self, periodo):
        """'Retenciones Esperadas': suma de monto_retenido (el cálculo legal
        real de Odoo, ver ve_wh_iva.py::_compute_monto_retenido -- ya usa el
        porcentaje_retencion real de cada documento, 75% o 100% según si
        trae nro_control, no un valor fijo) sobre TODO ve.wh.iva activo
        (state != anulado) de ESE período. Antes de 2026-08-14 usaba
        monto_iva_total * 0.75 (proyección a tasa fija, ignoraba el 100%
        sin N° Control) -- pedido explícito de la usuaria tras comparar
        contra el Excel de validación RIF/SENIAT: la Base debe ser el
        Esperado que Odoo ya calcula por factura, no una aproximación."""
        WH = self.env['ve.wh.iva']
        recs = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('state', '!=', 'anulado'),
        ])
        return round(sum(recs.mapped('monto_retenido')), 2)

    def _mes_label_corto(self, periodo):
        """'2026-06' → 'Jun/26' — para el eje de meses del gráfico de barras."""
        _MESES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
                  'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
        m = re.match(r'^(\d{4})-(\d{2})$', periodo or '')
        if not m:
            return periodo or ''
        anio, mes = m.group(1), int(m.group(2))
        if not 1 <= mes <= 12:
            return periodo
        return f'{_MESES[mes - 1]}/{anio[2:]}'

    # Paleta categórica (validada -- ver skill de dataviz, primeros slots del
    # tema por defecto, pasan CVD all-pairs en modo claro).
    _COLOR_PENDIENTE = '#2a78d6'   # azul
    _COLOR_SENIAT = '#eb6834'      # naranja

    _MIN_BAR_H = 3.0  # px -- piso visual para que un monto real chico (ej.
    # Pendiente de un solo mes contra Total SENIAT de todos) no desaparezca
    # del todo junto a barras cientos de veces más altas en la misma escala.

    _COLOR_DECLARADO = '#4a3aa7'  # violeta -- ver skill dataviz: en el orden
    # Pendiente/Conciliado/Declarado/SENIAT (blue/aqua/violet/orange) pasa
    # los 4 checks del validador; el 4to slot "natural" de la paleta
    # (amarillo) falla el piso de visión normal al lado del naranja de
    # SENIAT (ΔE 13.7 < 15), por eso se salta a violeta en su lugar.

    def _barras_n_series_geo(self, meses_valores, w, h, pad_x, pad_top, pad_bottom, n_series):
        """Geometría de barras agrupadas para una cantidad arbitraria de
        series -- usada por el gráfico de 6 series Estimado/Recibido/
        Pendiente/Declarado/SENIAT/Conciliado."""
        n = len(meses_valores)
        baseline_y = h - pad_bottom
        todos = [v for tupla in meses_valores for v in tupla if v is not None]
        maximo = max(todos, default=0.0) or 1.0
        plot_h = h - pad_top - pad_bottom
        plot_w = w - 2 * pad_x
        grupo_w = plot_w / n if n else plot_w
        gap_grupo = grupo_w * 0.30
        ancho_barras = grupo_w - gap_grupo
        barra_w = ancho_barras / n_series
        gap_barra = 1.5

        grupos = []
        for i, tupla in enumerate(meses_valores):
            x0 = pad_x + i * grupo_w + gap_grupo / 2
            barras = []
            for j, valor in enumerate(tupla):
                v = valor if valor is not None else 0.0
                pct = v / maximo
                bh = pct * plot_h
                if v > 0:
                    bh = max(bh, self._MIN_BAR_H)
                bx = x0 + j * (barra_w + gap_barra)
                by = baseline_y - bh
                barras.append({'x': bx, 'y': by, 'w': max(barra_w - gap_barra, 0.5), 'h': bh})
            grupos.append(barras)
        return grupos, baseline_y

    # Explicacion de cada serie del grafico de 6 barras (mensual y resumen
    # comparten el mismo orden Esperadas/Recibido/Pendiente/Declarado/
    # SENIAT/Conciliado) -- pedido explicito 2026-08-16 para que el tooltip
    # deje claro que mide cada barra y por que "Recibido" y "Pendiente c/Dif"
    # de otras pestanas no son directamente comparables (bases distintas).
    _EXPLICACIONES_6_SERIES = (
        'suma de monto_retenido (calculo legal, solo Contribuyente=S) de toda retencion activa.',
        'suma de monto_recibido (monto REAL del comprobante fisico) de retenciones Recibidas o Confirmadas, con o sin diferencia.',
        'suma de monto_retenido de retenciones sin comprobante (Esperado/Vencido) y aun no declaradas.',
        'monto declarado a SENIAT segun carga manual mensual (Forma 99 / c66).',
        'total de comprobantes SENIAT descargados para este periodo (con o sin match en Odoo).',
        'suma de monto_retenido de retenciones ya conciliadas con SENIAT: incluye con o sin '
        'diferencia de monto, con o sin comprobante fisico recibido -- todo lo que SENIAT '
        'confirmo contra Odoo, matcheado.',
    )

    def _n_series_barras_html(self, grupos, baseline_y, w, h, labels, meses_valores,
                               meses_full, colores, nombres):
        """Barras agrupadas para una cantidad arbitraria de series, colores/
        nombres parametrizados -- ver _barras_n_series_geo."""
        rects = []
        for grupo, tupla, mes_full in zip(grupos, meses_valores, meses_full):
            for idx, (barra, color, nombre, valor) in enumerate(zip(grupo, colores, nombres, tupla)):
                if barra['h'] <= 0:
                    continue
                explic = self._EXPLICACIONES_6_SERIES[idx] if idx < len(self._EXPLICACIONES_6_SERIES) else ''
                titulo = f'{mes_full} — {nombre}: Bs.{self._fmt_monto(valor or 0.0, 2)}&#10;{explic}'
                rects.append(
                    f'<rect x="{barra["x"]:.1f}" y="{barra["y"]:.1f}" '
                    f'width="{barra["w"]:.1f}" height="{barra["h"]:.1f}" '
                    f'rx="1.5" fill="{color}"><title>{titulo}</title></rect>'
                )
        svg = (
            f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
            f'style="width:100%; height:{h}px; display:block;">'
            f'<line x1="0" y1="{baseline_y:.1f}" x2="{w}" y2="{baseline_y:.1f}" '
            f'stroke="#e1e0d9" stroke-width="1"/>'
            + ''.join(rects) +
            '</svg>'
        )
        spans = [f'<span>{lbl}</span>' if lbl else '<span></span>' for lbl in labels]
        labels_row = (
            '<div class="d-flex justify-content-between px-1" '
            'style="font-size:0.58rem; color:#8a8f98;">' + ''.join(spans) + '</div>'
        )
        leyenda_items = ''.join(
            f'<span><span style="display:inline-block; width:8px; height:8px; '
            f'border-radius:2px; background:{color};"></span> {nombre}</span>'
            for color, nombre in zip(colores, nombres)
        )
        leyenda = f'<div class="d-flex gap-3 mb-1" style="font-size:0.68rem;">{leyenda_items}</div>'
        return f'<div>{leyenda}{svg}{labels_row}</div>'

    def _serie_meses_anio(self, anio):
        """Períodos agrupados por mes ('YYYY-MM'), acotado a UN año --
        pedido explícito 2026-08-07 para el gráfico Estimado/Recibido/
        Pendiente/Declarado/SENIAT (año en curso, no todo el histórico)."""
        periodos = self.env['ve.conciliacion.periodo'].search([
            ('company_id', '=', self.env.company.id),
            ('periodo', '=like', f'{anio}-%'),
        ])
        por_mes = {}
        for p in periodos:
            por_mes.setdefault(p.periodo, self.env['ve.conciliacion.periodo'])
            por_mes[p.periodo] |= p
        meses_ordenados = sorted(por_mes.keys())
        return [(m, por_mes[m]) for m in meses_ordenados]

    def _serie_valor_declarado_auto(self, periodo):
        """"Declarado" calculado desde Odoo (fallback cuando la compañía NO
        tiene activo ve_declarado_manual): monto de retenciones con Estado
        Declaración = Declarado, para ESE período -- mismo domain que
        wh_iva_declaradas_ids (ve_conciliacion.py)."""
        WH = self.env['ve.wh.iva']
        recs = WH.search([
            ('conciliacion_id', '=', periodo.id),
            ('estado_declaracion', '=', 'declarado'),
        ])
        return sum(recs.mapped('monto_retenido'))

    # Colores para el gráfico Estimado/Recibido/Pendiente/Declarado/SENIAT/
    # Conciliado -- validados con la skill dataviz (scripts/
    # validate_palette.js), los 6 juntos en el orden real de las barras.
    # Todos los checks pasan (CVD ΔE >= 8, piso visión normal >= 15); el
    # WARN de contraste de #1baf7a contra el fondo es preexistente (ya
    # estaba así con 5 colores) y queda cubierto por la leyenda con texto
    # y los tooltips con el monto exacto, no color solo.
    _COLOR_ESTIMADO = '#b8860b'
    _COLOR_RECIBIDO = '#1baf7a'
    _COLOR_CONCILIADO = '#c23b7e'

    @api.depends()
    def _compute_estimado_recibido(self):
        # Vuelto a dinámico 2026-08-27 -- el corte fijo a Jun (pedido
        # puntual 2026-08-14 para no mezclar el Libro de Ventas validado
        # de Cementos, capado a Ene-Jun, con facturación real posterior)
        # quedó desactualizado: Cementos está congelado desde el 16-ago y
        # dejó de sincronizarse, no hay ya ningún cliente activo que
        # dependa del rango fijo. Mismo criterio que _get_rango_ytd().
        hoy = fields.Date.today()
        ANIO = hoy.year
        meses = self._serie_meses_anio(ANIO)
        meses = [m for m in meses if m[0] <= f'{ANIO}-{hoy.month:02d}']
        # h=340 (antes 240, subido de nuevo 2026-08-14) -- pedido explícito:
        # con 6 series, "Pendiente por Recibir" (la más chica casi siempre)
        # seguía perdiéndose de vista incluso con el aumento anterior
        # (160->240, 2026-08-10).
        dims = {'w': 560, 'h': 340, 'pad_x': 4, 'pad_top': 8, 'pad_bottom': 4}
        company = self.env.company
        Declarado = self.env['ve.declarado.mensual']

        meses_valores = []
        labels = []
        meses_full = []
        # Riesgo de Sanción (Declarado no Recibido) y Subdeclaración (Recibido
        # no Declarado) -- pedido explícito 2026-08-10, mismos 2 indicadores
        # que ya existían en el Excel de Cementos (hoja "Analisis", columnas
        # "Declarado no Recibido"/"Recibido no Declarado"). Una fila por mes
        # (pedido explícito, reemplaza el total YTD agregado que se probó
        # primero) -- declarado y recibido son del MISMO mes, así que un mes
        # dado cae en Riesgo de Sanción (declarado > recibido) O en
        # Subdeclaración (recibido > declarado), nunca los dos a la vez; si
        # son iguales no genera fila. Cantidad queda N/D -- "Declarado" es
        # un total mensual sin desglose por comprobante (mismo motivo que
        # TAC en "Índices PROYECCION"), no se puede saber CUÁLES documentos
        # componen el excedente.
        filas_sancion = []
        filas_subdeclaracion = []
        # Cantidad de comprobantes (no solo monto) para las tarjetas IOC/BDS
        # -- pedido explícito 2026-08-22. TAC se queda sin cantidad a
        # propósito: "Declarado" es un total mensual sin desglose por
        # comprobante (ver comentario de filas_sancion arriba), no hay un
        # conteo real que mostrar.
        base_cnt_ytd = 0
        recibido_cnt_ytd = 0
        seniat_cnt_ytd = 0
        for mes_str, quincenas in meses:
            estimado = round(sum(self._serie_valor_estimado(p) for p in quincenas), 2)
            recibido = round(sum(self._serie_valor_recibido(p) for p in quincenas), 2)
            pendiente = round(sum(self._serie_valor_pendiente_total(p) for p in quincenas), 2)
            conciliado = round(sum(self._serie_valor_conciliado(p) for p in quincenas), 2)
            seniat = round(sum(quincenas.mapped('total_seniat')), 2)
            base_cnt_ytd += sum(self._serie_cantidad_estimado(p) for p in quincenas)
            recibido_cnt_ytd += sum(self._serie_cantidad_recibido(p) for p in quincenas)
            seniat_cnt_ytd += sum(quincenas.mapped('n_seniat'))
            if company.ve_declarado_manual:
                anio_i, mes_i = int(mes_str[:4]), int(mes_str[5:7])
                rec_decl = Declarado.search([
                    ('company_id', '=', company.id),
                    ('anio', '=', anio_i), ('mes', '=', mes_i),
                ], limit=1)
                declarado = round(rec_decl.monto_declarado, 2) if rec_decl else 0.0
            else:
                declarado = round(sum(self._serie_valor_declarado_auto(p) for p in quincenas), 2)
            meses_valores.append((estimado, recibido, pendiente, declarado, seniat, conciliado))
            mes_label = self._mes_label_corto(mes_str)
            labels.append(mes_label)
            meses_full.append(mes_str)
            excedente = round(declarado - recibido, 2)
            if excedente > 0:
                filas_sancion.append((mes_label, excedente))
            elif excedente < 0:
                filas_subdeclaracion.append((mes_label, -excedente))

        colores = (self._COLOR_ESTIMADO, self._COLOR_RECIBIDO, self._COLOR_PENDIENTE,
                   self._COLOR_DECLARADO, self._COLOR_SENIAT, self._COLOR_CONCILIADO)
        nombres = ('Retenciones Esperadas', 'Recibido', 'Pendiente por Recibir',
                   'Declarado', 'Total SENIAT', 'Conciliado')
        grupos, baseline_y = self._barras_n_series_geo(meses_valores, n_series=6, **dims)
        html = self._n_series_barras_html(
            grupos, baseline_y, dims['w'], dims['h'], labels, meses_valores, meses_full,
            colores, nombres)

        riesgo_html = self._riesgo_subdeclaracion_html(filas_sancion, filas_subdeclaracion)

        # ── IOC/TAC/BDS, universo PROYECCIÓN -- pedido explícito 2026-08-11,
        # mismo cálculo que ya se usaba en la propuesta comercial en
        # PowerPoint: Base = Retenciones Esperadas YTD (75% del IVA causado,
        # suma de `estimado` de todos los meses de arriba). IOC Logrado =
        # Recibido YTD (con comprobante). TAC Logrado = Declarado YTD (total
        # mensual, sin desglose por comprobante -- mismo motivo que la nota
        # de Riesgo de Sanción de arriba). BDS Logrado = Total SENIAT YTD.
        base_ytd = round(sum(m[0] for m in meses_valores), 2)
        recibido_ytd = round(sum(m[1] for m in meses_valores), 2)
        declarado_ytd = round(sum(m[3] for m in meses_valores), 2)
        seniat_ytd = round(sum(m[4] for m in meses_valores), 2)
        ioc_html = self._ioc_tac_bds_html(base_ytd, recibido_ytd, declarado_ytd, seniat_ytd,
                                           base_cnt=base_cnt_ytd, ioc_cnt=recibido_cnt_ytd,
                                           bds_cnt=seniat_cnt_ytd)

        for rec in self:
            rec.estimado_recibido_svg_html = html
            rec.riesgo_sancion_mensual_html = riesgo_html
            rec.ioc_tac_bds_html = ioc_html

    def _ioc_tac_bds_html(self, base, ioc_logrado, tac_logrado, bds_logrado,
                          base_cnt=None, ioc_cnt=None, bds_cnt=None):
        """3 tarjetas (IOC/TAC/BDS) sobre la Base de Retenciones Esperadas
        YTD -- mismo cálculo/formato que la lámina de propuesta comercial
        en PowerPoint (gen_propuesta_cementos_pptx.py, universo
        PROYECCIÓN), ahora en vivo dentro del Dashboard.

        ioc_cnt/bds_cnt (pedido explícito 2026-08-22, corregido en la
        misma ronda -- la primera versión mostraba cantidad/monto de lo
        LOGRADO, la usuaria pidió lo FALTANTE): cantidad de comprobantes
        LOGRADOS en esas 2 tarjetas, usada junto con base_cnt para
        calcular "cuántos faltan" (base_cnt - logrado_cnt) y mostrarlo
        junto al monto que falta. TAC se queda sin cantidad a propósito
        -- "Declarado" es un total mensual sin desglose por comprobante
        (ver comentario en _compute_estimado_recibido), no hay un conteo
        real que mostrar ahí."""
        def _tarjeta(color, titulo, logrado, descripcion, cnt=None):
            pct = (logrado / base * 100) if base else 0.0
            faltante = base - logrado
            pct_falta = 100 - pct
            cnt_faltan = (base_cnt - cnt) if (base_cnt is not None and cnt is not None) else None
            cnt_txt = f'{cnt_faltan:,}'.replace(',', '.') + ' comp. — ' if cnt_faltan is not None else ''
            return (
                '<div style="flex:1; min-width:0; background:#fff; border-radius:8px; '
                f'padding:12px 14px; border-left:4px solid {color};">'
                f'<div class="small fw-bold text-uppercase" style="color:{color}; font-size:0.68rem;">'
                f'{escape(titulo)}</div>'
                f'<div class="fw-bold" style="font-size:1.6rem; color:{color};">{pct:.1f}%</div>'
                # "Faltan" agrandado y en negrilla (pedido explícito
                # 2026-08-22) -- antes font-size:0.7rem + text-muted, se
                # perdía al lado del % grande de arriba. Cantidad que
                # falta (cnt_txt) antepuesta al monto, no la cantidad
                # lograda.
                '<div class="fw-bold" style="font-size:0.95rem; color:#333; margin-top:3px;">'
                f'Faltan {cnt_txt}Bs.{self._fmt_monto(faltante, 2)} ({pct_falta:.1f}%)</div>'
                f'<div class="text-muted" style="font-size:0.68rem; margin-top:2px;">{escape(descripcion)}</div>'
                '</div>'
            )
        tarjetas = (
            _tarjeta('#17A2B8', 'Obtención de Comprobantes (IOC)', ioc_logrado,
                     'de retenciones esperadas por obtener comprobante.', cnt=ioc_cnt)
            + _tarjeta('#28A745', 'Aprovechamiento de Créditos (TAC)', tac_logrado,
                       'de crédito fiscal potencial sin declarar/aprovechar.')
            + _tarjeta('#B5474D', 'Brecha vs. Portal SENIAT (BDS)', bds_logrado,
                       'que los clientes aún no reportan al SENIAT.', cnt=bds_cnt)
        )
        return (
            f'<div class="text-muted mb-1" style="font-size:0.7rem;">'
            f'Base (Retenciones Esperadas YTD): Bs.{self._fmt_monto(base, 2)}</div>'
            f'<div style="display:flex; gap:10px;">{tarjetas}</div>'
        )

    def _riesgo_subdeclaracion_html(self, filas_sancion, filas_subdeclaracion):
        """2 tablitas lado a lado (Riesgo de Sanción / Subdeclaración), una
        fila por mes en cada una -- pedido explícito 2026-08-10, reemplaza
        el total YTD agregado. Ambas listas ya vienen filtradas a los meses
        con excedente != 0 (ver _compute_estimado_recibido)."""
        def _tabla(titulo, color, filas):
            if not filas:
                filas_html = (
                    '<div class="text-muted" style="font-size:0.75rem; padding:6px 0;">'
                    'Sin excedente en los meses mostrados</div>'
                )
            else:
                rows = ''.join(
                    '<tr>'
                    f'<td style="padding:3px 1px 3px 0; border-top:1px solid #EBFFFF; text-align:left; white-space:nowrap;">{escape(mes_label)}</td>'
                    '<td style="padding:3px 1px; border-top:1px solid #EBFFFF; text-align:left;">:</td>'
                    f'<td style="padding:3px 0 3px 10px; border-top:1px solid #EBFFFF; text-align:right; white-space:nowrap;">Bs.{self._fmt_monto(monto, 2)}</td>'
                    '</tr>'
                    for mes_label, monto in filas
                )
                filas_html = f'<table style="font-size:0.8rem;"><tbody>{rows}</tbody></table>'
            return (
                '<div style="flex:1; min-width:0;">'
                f'<div class="small fw-bold text-uppercase mb-1" style="color:{color}; font-size:0.7rem;">'
                f'{escape(titulo)}</div>'
                f'{filas_html}'
                '</div>'
            )
        tabla_sancion = _tabla('Riesgo de Sanción', '#b5474d', filas_sancion)
        tabla_subdeclaracion = _tabla('Subdeclaración', '#eb6834', filas_subdeclaracion)
        return (
            '<div style="display:flex; gap:16px;">'
            f'{tabla_sancion}'
            '<div style="width:1px; background-color:#EBFFFF;"></div>'
            f'{tabla_subdeclaracion}'
            '</div>'
        )

    def _solo_seniat_sin_match_bs(self):
        """Devuelve (sin_match, total_general) -- monto SENIAT sin
        contraparte en Odoo, calculado EN VIVO por RIF + N° Control (N1) o
        RIF + N° Factura (N2), y el total general de retenciones SENIAT
        (mismo universo, para la barra "Con Match" = total - sin_match --
        pedido explícito 2026-08-14). NO depende del campo `estado` de
        ve.seniat.retencion (que solo pasa a 'sin_match' si alguien ya
        corrió "Conciliar SENIAT" sobre ese período).

        Bug real encontrado 2026-08-05 (Cementos, pedido explícito de la
        usuaria tras notar el KPI demasiado chico): 94% de las retenciones
        SENIAT reales (15.363 de 16.390, Bs. 2.844M de Bs. 2.958M total)
        seguían en estado 'cargado' -- solo noviembre 2025 había pasado
        por conciliación real, todos los meses de diciembre a junio nunca
        se tocaron. El KPI original (filtrando estado='sin_match') era
        ciego a ese 94% -- mostraba Bs. 89,8M cuando el hueco real podía
        ser mucho mayor. No se corre "Conciliar SENIAT" acá a propósito
        (pedido explícito: eso se hace más adelante, después de cargar
        los libros de ventas) -- este método solo LEE, nunca escribe.

        Corregido 2026-08-11: este método tenía su PROPIA copia de
        _norm_rif/_norm_ctrl (solo strip+upper, sin quitar ceros a la
        izquierda ni guiones) -- una duplicación que quedó desincronizada
        del matcher real (ve_conciliacion.py::_do_conciliar) cuando ese se
        mejoró el mismo día (72-77% más de conciliaciones reales, medido
        con datos de Cementos). El KPI parecía "no variar" porque seguía
        comparando con la normalización vieja. Ahora reusa las funciones
        de ve.conciliacion.periodo (misma fuente de verdad, un solo lugar
        para mantener) y agrega el respaldo N2 por N° de Factura."""
        Concil = self.env['ve.conciliacion.periodo']
        norm_rif = Concil._norm_rif
        norm_ctrl = Concil._norm_ctrl
        norm_factura = Concil._norm_factura

        WhIva = self.env['ve.wh.iva']
        wh_activos = WhIva.search([
            ('company_id', '=', self.env.company.id),
            ('state', '!=', 'anulado'),
        ])
        seniat_recs = self.env['ve.seniat.retencion'].search([
            ('company_id', '=', self.env.company.id),
        ])

        # Bug real confirmado 2026-08-22 (Vencement): la versión anterior
        # solo miraba si la clave (RIF+Control o RIF+Factura) EXISTÍA del
        # lado Odoo, sin replicar la cascada real de _do_conciliar --
        # contaba como "con match" 10 registros (Bs. 1.876.142,14) que en
        # realidad quedan "Solo SENIAT" tanto en el Excel como en la dona
        # de Salud de Conciliación, porque SENIAT reutiliza un mismo
        # N°Control entre 2-3 comprobantes reales distintos del mismo
        # agente (mismo patrón ya documentado en _do_conciliar 2026-08-05)
        # y ve_conciliacion.py::_do_conciliar se niega a adivinar cuál es
        # el correcto -- deja a TODOS sin vincular.
        #
        # Para no volver a desincronizarse (ya pasó una vez, ver docstring
        # de arriba), esto ahora REPLICA la cascada completa de
        # _do_conciliar (N1 RIF+Control, desambiguar por N° Factura si hay
        # más de 1 candidato, fallback N2 RIF+Factura, "más de 1 candidato
        # tras ambos niveles" = sin match) en vez de un atajo por
        # conteo -- sigue sin escribir nada, es una simulación de solo
        # lectura. Si `_do_conciliar` cambia su cascada, hay que replicar
        # el cambio acá también (no hay forma de compartir código sin
        # tocar ese método crítico -- ver también _CONCIL_CONCILIADA_
        # ESTADOS, mismo patrón de duplicación consciente en este archivo).
        by_ctrl = {}
        by_factura = {}
        seniat_norm_factura = {}
        for s in seniat_recs:
            s_rif = norm_rif(s.rif_agente)
            s_ctrl = norm_ctrl(s.nro_control)
            s_factura = norm_factura(s.nro_documento)
            seniat_norm_factura[s.id] = s_factura
            if s_ctrl:
                by_ctrl.setdefault((s_rif, s_ctrl), []).append(s)
            if s_factura:
                by_factura.setdefault((s_rif, s_factura), []).append(s)

        SeniatEmpty = self.env['ve.seniat.retencion']
        seniat_matched_ids = set()
        for wh in wh_activos:
            wh_rif = norm_rif(wh.rif)
            wh_ctrl_norm = norm_ctrl(wh.nro_control)
            wh_factura_raw = (wh.invoice_id.name if wh.invoice_id else (wh.nro_documento or '')).strip().upper()
            wh_factura_norm = norm_factura(wh_factura_raw)

            seniat_match = SeniatEmpty
            if wh_ctrl_norm:
                candidatos_list = [
                    s for s in by_ctrl.get((wh_rif, wh_ctrl_norm), [])
                    if s.id not in seniat_matched_ids
                ]
                if len(candidatos_list) > 1 and wh_factura_norm:
                    por_doc = [s for s in candidatos_list if seniat_norm_factura[s.id] == wh_factura_norm]
                    if por_doc:
                        candidatos_list = por_doc
                if candidatos_list:
                    seniat_match = SeniatEmpty.browse([s.id for s in candidatos_list])

            if not seniat_match and wh_factura_norm:
                candidatos_list = [
                    s for s in by_factura.get((wh_rif, wh_factura_norm), [])
                    if s.id not in seniat_matched_ids
                ]
                if candidatos_list:
                    seniat_match = SeniatEmpty.browse([s.id for s in candidatos_list])

            if len(seniat_match) == 1:
                seniat_matched_ids.add(seniat_match.id)

        total_general = 0.0
        sin_match = 0.0
        for s in seniat_recs:
            total_general += s.monto_retenido
            if s.id not in seniat_matched_ids:
                sin_match += s.monto_retenido
        return sin_match, total_general

    def _seniat_match_bars_html(self, con_match_bs, sin_match_bs, h=113):
        """Dos barras verticales lado a lado (Con Match vs Sin Match),
        mismo patrón que _sanciones_bars_html -- pedido explícito
        2026-08-14: dar contexto visual al monto único de "Sin match en
        tus registros", mostrando qué proporción representa del total de
        retenciones que SENIAT ya confirma. h=113 (antes 90, +25%) y
        tooltips ampliados (monto + % del total + qué significa cada
        barra) -- mismo pedido explícito, alineado en altura con
        _resumen_ytd_bars_html (h=175, misma proporción +25%) para que
        ambos gráficos de la sección RESUMEN YTD se lean parejos."""
        maximo = max(con_match_bs, sin_match_bs) or 1.0
        h_match = max(2, round((con_match_bs / maximo) * h)) if con_match_bs else 0
        h_sin = max(2, round((sin_match_bs / maximo) * h)) if sin_match_bs else 0
        total = con_match_bs + sin_match_bs
        pct_sin = (sin_match_bs / total * 100) if total else 0.0
        pct_con = (con_match_bs / total * 100) if total else 0.0
        tt_con = (f'Con Match: Bs.{self._fmt_monto(con_match_bs)} ({pct_con:.1f}% del total) '
                  f'-- ya vinculado a un registro en SmartIVA')
        tt_sin = (f'Sin Match: Bs.{self._fmt_monto(sin_match_bs)} ({pct_sin:.1f}% del total) '
                  f'-- SENIAT lo confirma pero SmartIVA no tiene ningun registro vinculado')
        return (
            f'<div style="display:flex; flex-direction:column; align-items:center; gap:4px;">'
            f'<div class="d-flex align-items-end justify-content-center gap-2" style="height:{h}px;">'
            f'<div style="width:34px; height:{h_match}px; background-color:#2f7d4f; '
            f'border-radius:3px 3px 0 0;" title="{tt_con}"></div>'
            f'<div style="width:34px; height:{h_sin}px; background-color:#eb6834; '
            f'border-radius:3px 3px 0 0;" title="{tt_sin}"></div>'
            '</div>'
            '<div class="d-flex gap-2" style="font-size:0.62rem;">'
            '<span class="text-muted">Con match</span><span class="text-muted">Sin match</span>'
            '</div>'
            f'<div class="fw-bold text-muted" style="font-size:0.66rem;">{pct_sin:.1f}% sin match</div>'
            '</div>'
        )

    def _fmt_miles_millones(self, v):
        """1234567890.4 → '1,23' (miles de millones = /1e9, 2 decimales,
        coma decimal venezolana) -- pedido explícito 2026-08-22 para la
        etiqueta arriba de cada barra de RESUMEN YTD (el monto
        exacto en Bs sigue disponible en el tooltip)."""
        return f'{v / 1e9:.2f}'.replace('.', ',')

    def _resumen_ytd_bars_html(self, valores, cantidades=None, h=260):
        """5 barras verticales lado a lado (Esperadas/Recibido/Declarado/
        SENIAT/Conciliado), un total YTD por categoría -- mismos colores/
        orden que el gráfico mensual (_compute_estimado_recibido, que sí
        conserva las 6 series/Pendiente) para que se lean como el mismo
        lenguaje visual, pero agregado en vez de por mes. Mismo patrón que
        _seniat_match_bars_html/_sanciones_bars_html (altura proporcional
        al máximo, piso 2px). h=260 (antes 175, pedido explícito
        2026-08-22 -- "que se vea holgado"; label_h reserva espacio aparte
        arriba de cada barra para el monto en miles de millones, pedido en
        la misma ronda).

        Barra "Pendiente" QUITADA de ESTE gráfico (pedido explícito
        2026-08-22, misma ronda): tenía su propia definición
        (_serie_valor_pendiente_total) que no coincidía con "Faltan" de la
        tarjeta IOC -- el faltante real ya se lee como la diferencia
        visual entre "Esperadas" y "Recibido" acá mismo, que sí coincide
        exacto con IOC (ver comentario en _compute_resumen_ytd). El
        gráfico MENSUAL (_compute_estimado_recibido) conserva sus 6 series
        con Pendiente -- no se tocó, es un gráfico distinto.

        cantidades (pedido explícito 2026-08-22): tupla paralela a
        `valores`, cantidad de comprobantes detrás de cada barra para el
        tooltip "Cantidad/Monto" -- None en la posición de una barra sin
        conteo real (Declarado: total mensual sin desglose por
        comprobante, mismo motivo que en IOC/TAC/BDS)."""
        nombres = ('Esperadas', 'Recibido', 'Declarado', 'SENIAT', 'Conciliado')
        colores = (self._COLOR_ESTIMADO, self._COLOR_RECIBIDO,
                   self._COLOR_DECLARADO, self._COLOR_SENIAT, self._COLOR_CONCILIADO)
        # _EXPLICACIONES_6_SERIES sigue las 6 series del gráfico mensual
        # (Esperadas/Recibido/Pendiente/Declarado/SENIAT/Conciliado) -- se
        # salta el índice 2 (Pendiente) acá para no duplicar los textos.
        explicaciones = tuple(
            e for i, e in enumerate(self._EXPLICACIONES_6_SERIES) if i != 2)
        if cantidades is None:
            cantidades = (None,) * len(valores)
        maximo = max(valores) or 1.0
        label_h = 16
        barras = []
        etiquetas = []
        # Ancho de barra/etiqueta subido de 46px a 58px (pedido explícito
        # 2026-08-14, ronda 2 -- todavía se veía apretado) -- gap-3 se
        # mantiene, ahora hay más ancho de columna disponible (col-lg-7).
        for nombre, color, valor, cnt, explic in zip(nombres, colores, valores, cantidades, explicaciones):
            alto = max(2, round((valor / maximo) * h)) if valor else 0
            monto_mm = self._fmt_miles_millones(valor)
            cnt_txt = (f'{cnt:,}'.replace(',', '.') + ' comp. — ') if cnt is not None else ''
            barras.append(
                f'<div style="display:flex; flex-direction:column; align-items:center; '
                f'justify-content:flex-end; width:58px; height:{h + label_h}px;">'
                f'<div style="font-size:0.64rem; font-weight:700; color:#333; margin-bottom:2px; '
                f'white-space:nowrap;">{monto_mm}</div>'
                f'<div style="width:58px; height:{alto}px; background-color:{color}; '
                f'border-radius:3px 3px 0 0;" title="{nombre} (YTD): {cnt_txt}Bs.{self._fmt_monto(valor)}&#10;{explic}"></div>'
                '</div>')
            etiquetas.append(
                f'<span style="width:58px; text-align:center; overflow-wrap:break-word;">{nombre}</span>')
        return (
            '<div style="display:flex; flex-direction:column; align-items:center; gap:4px; width:100%;">'
            '<div class="text-muted" style="font-size:0.6rem;">(montos en miles de millones de Bs.)</div>'
            f'<div class="d-flex align-items-end justify-content-center gap-3" style="height:{h + label_h}px;">'
            + ''.join(barras) +
            '</div>'
            '<div class="d-flex gap-3" style="font-size:0.62rem; line-height:1.15;">'
            + ''.join(etiquetas) +
            '</div>'
            '</div>'
        )

    @api.depends()
    def _compute_resumen_ytd(self):
        company = self.env.company
        # Vuelto a dinámico 2026-08-27, mismo criterio que _get_rango_ytd()
        # (ver ahí el porqué) -- reusa ese método para que este resumen y
        # el resto de los KPI "YTD" del Dashboard midan exactamente la
        # misma ventana y nunca se descuadren entre sí. El límite superior
        # explícito sigue siendo necesario (bug encontrado 2026-08-16: sin
        # tope, Declarado/SENIAT sumaban también facturación real de uso
        # corriente de Odoo fuera de la ventana YTD).
        year_start, year_end = self._get_rango_ytd()
        # Bug real encontrado 2026-08-27: filtraba por el campo TEXTO
        # `periodo` ('yyyy-mm', granularidad de mes) mientras que la dona
        # de Salud de Conciliación (_compute_salud_conciliacion) y Margen
        # C/D (_compute_margen_cd) filtran por `fecha_fin` (fecha exacta)
        # -- en los bordes del rango YTD podían seleccionar conjuntos de
        # períodos LIGERAMENTE distintos (ej. la quincena en curso, cuyo
        # fecha_fin cae después de "hoy" pero cuyo mes ya es <= year_end),
        # así que "Esperadas" de este resumen no cuadraba con el total de
        # la dona "Sin Conciliar" aunque ambos dijeran ser el mismo YTD.
        # Unificado a fecha_fin, igual que el resto.
        periodos = self.env['ve.conciliacion.periodo'].search([
            ('company_id', '=', company.id),
            ('fecha_fin', '>=', year_start),
            ('fecha_fin', '<=', year_end),
        ])
        # Barra "Pendiente" QUITADA (pedido explícito 2026-08-22): tenía su
        # propia definición (_serie_valor_pendiente_total, esperado/vencido
        # sin declarar) que NO coincidía con "Faltan" de la tarjeta IOC
        # (base − recibido) -- confundía comparar las dos. El faltante real
        # ya se lee como la diferencia visual entre "Esperadas" y
        # "Recibido" de este mismo gráfico, que por construcción SÍ es
        # exactamente igual a "Faltan" de IOC (mismas sumas estimado_ytd/
        # recibido_ytd que usa _compute_estimado_recibido para
        # base_ytd/recibido_ytd) -- verificado 2026-08-22: Bs.5.961.588.612,40
        # − Bs.3.943.902.106,33 = Bs.2.017.686.506,07, igual a "Faltan" de
        # IOC al centavo.
        estimado = round(sum(self._serie_valor_estimado(p) for p in periodos), 2)
        recibido = round(sum(self._serie_valor_recibido(p) for p in periodos), 2)
        conciliado = round(sum(self._serie_valor_conciliado(p) for p in periodos), 2)
        seniat = round(sum(periodos.mapped('total_seniat')), 2)
        if company.ve_declarado_manual:
            Declarado = self.env['ve.declarado.mensual']
            recs_decl = Declarado.search([
                ('company_id', '=', company.id), ('anio', '=', ANIO), ('mes', '<=', 6),
            ])
            declarado = round(sum(recs_decl.mapped('monto_declarado')), 2)
        else:
            declarado = round(sum(self._serie_valor_declarado_auto(p) for p in periodos), 2)

        # Cantidades para el tooltip "Cantidad/Monto" (pedido explícito
        # 2026-08-22) -- Declarado se queda en None a propósito, mismo
        # motivo que en IOC/TAC/BDS: es un total mensual sin desglose por
        # comprobante.
        cnt_estimado = sum(self._serie_cantidad_estimado(p) for p in periodos)
        cnt_recibido = sum(self._serie_cantidad_recibido(p) for p in periodos)
        cnt_conciliado = sum(self._serie_cantidad_conciliado(p) for p in periodos)
        cnt_seniat = sum(periodos.mapped('n_seniat'))

        bars_html = self._resumen_ytd_bars_html(
            (estimado, recibido, declarado, seniat, conciliado),
            (cnt_estimado, cnt_recibido, None, cnt_seniat, cnt_conciliado))
        for rec in self:
            rec.resumen_ytd_bars_html = bars_html

    @api.depends()
    def _compute_solo_seniat_sin_match(self):
        sin_match, total_general = self._solo_seniat_sin_match_bs()
        con_match = total_general - sin_match
        bars_html = self._seniat_match_bars_html(con_match, sin_match)
        for rec in self:
            rec.solo_seniat_ytd_bs = sin_match
            rec.seniat_total_bs = total_general
            rec.seniat_match_bars_html = bars_html

    # ── Compute: KPI 4 — Sanciones del Año (Pendientes vs Impugnadas) ────────
    @staticmethod
    def _fmt_monto(v, decimals=0):
        """1234567.8 → '1.234.568' (formato venezolano: punto miles, coma decimal)."""
        s = f'{v:,.{decimals}f}'
        return s.replace(',', '§').replace('.', ',').replace('§', '.')

    def _sanciones_bars_html(self, pend_bs, impu_bs, h=36):
        """Dos barras pegadas (sin separación), mismo alto que los sparklines
        de línea (h=36) para que la base coincida visualmente con las
        tarjetas KPI 1-3 en la misma fila.

        La altura es directamente proporcional al monto (sin piso aditivo):
        con un piso como '6 + pct*30', un monto que es solo el 15% del
        máximo salía con ~35% de la altura — visualmente subestimaba la
        diferencia real entre las dos barras. Solo se usa un piso mínimo de
        2px para que un monto pequeño pero no cero siga siendo visible."""
        maximo = max(pend_bs, impu_bs) or 1.0
        h_pend = max(2, round((pend_bs / maximo) * h)) if pend_bs else 0
        h_impu = max(2, round((impu_bs / maximo) * h)) if impu_bs else 0
        return (
            f'<div class="d-flex align-items-end justify-content-center" style="height:{h}px;">'
            f'<div style="width:52px; height:{h_pend}px; background-color:#b5474d; '
            f'border-radius:3px 0 0 0;" title="Pendientes: Bs.{self._fmt_monto(pend_bs)}"></div>'
            f'<div style="width:52px; height:{h_impu}px; background-color:#d6a13a; '
            f'border-radius:0 3px 0 0;" title="Impugnadas: Bs.{self._fmt_monto(impu_bs)}"></div>'
            '</div>'
        )

    @api.depends()
    def _compute_sanciones(self):
        year_start, year_end = self._get_rango_ytd()
        Line = self.env['ve.sancion.iva.line']
        pend_lineas = Line.search([
            ('sancion_id.estado', '=', 'pendiente'),
            ('sancion_id.fecha', '>=', str(year_start)),
            ('sancion_id.fecha', '<=', str(year_end)),
        ])
        impu_lineas = Line.search([
            ('sancion_id.estado', '=', 'impugnada'),
            ('sancion_id.fecha', '>=', str(year_start)),
            ('sancion_id.fecha', '<=', str(year_end)),
        ])
        # monto_bs_hoy en vez de monto_bs: la multa se fija en divisa y el
        # Bs se recalcula con la tasa vigente hasta que se paga (Art.
        # 96/98/108 COT) — ver ve.sancion.iva.line._compute_monto_bs_hoy.
        pend_bs = sum(pend_lineas.mapped('monto_bs_hoy'))
        pend_eur = sum(pend_lineas.mapped('monto_eur'))
        impu_bs = sum(impu_lineas.mapped('monto_bs_hoy'))
        impu_eur = sum(impu_lineas.mapped('monto_eur'))
        bars_html = self._sanciones_bars_html(pend_bs, impu_bs)

        for rec in self:
            rec.total_sanciones_ano_bs = pend_bs + impu_bs
            rec.total_sanciones_ano_eur = pend_eur + impu_eur
            rec.sancion_pend_bs = pend_bs
            rec.sancion_pend_eur = pend_eur
            rec.sancion_impu_bs = impu_bs
            rec.sancion_impu_eur = impu_eur
            rec.sanciones_bars_html = bars_html

    # ── Compute: Cascada de Liquidez (Período + YTD) ────────────────────────
    @api.depends()
    def _compute_liquidez(self):
        hoy = fields.Date.today()
        year_start, year_end = self._get_rango_ytd()
        # Incluye también comprobantes SIN conciliacion_id asignado (aún no
        # reconciliados a ningún período — ver comentario "se asignará al
        # período activo al conciliar"): si se filtrara solo por
        # conciliacion_id.fecha_inicio, un comprobante vencido que todavía
        # no tiene período asignado desaparecería de la Cascada YTD aunque
        # sí cuente en "Vencidos"/"No Recibido" (que no dependen de
        # conciliacion_id) — dos vencidos demo, solo se veía uno.
        domain_ytd = [
            ('company_id', '=', self.env.company.id),
            '|',
                ('conciliacion_id', '=', False),
                '&', ('conciliacion_id.fecha_inicio', '>=', year_start),
                     ('conciliacion_id.fecha_inicio', '<=', year_end),
        ]
        periodos_anio = self.env['ve.conciliacion.periodo'].search([
            ('fecha_fin', '>=', year_start),
            ('fecha_fin', '<=', year_end),
            ('company_id', '=', self.env.company.id),
        ])
        debito_ytd = sum(
            p.declaracion_iva_id.campo_49 for p in periodos_anio if p.declaracion_iva_id
        )
        con_ytd = sum(self.env['ve.wh.iva'].search(
            domain_ytd + [('estado_recepcion', 'in', ['confirmado', 'confirmado_dif'])]
        ).mapped('monto_retenido'))
        # "Sin comprobante" = las 3 categorías no confirmadas del ciclo de
        # vida (mismo criterio que "Crédito del Período por Estado" en
        # Operativo): vencido (esperado/vencido YA fuera de plazo) + no
        # recibido (esperado, plazo todavía no vence) + no confirmado
        # (borrador — ya lo entregó el cliente, solo falta confirmarlo
        # internamente, cuenta igual aunque su plazo no haya vencido).
        sin_ytd_venc = sum(self.env['ve.wh.iva'].search(
            domain_ytd + [
                ('state', 'in', ['esperado', 'vencido']),
                ('fecha_vencimiento_entrega', '<=', hoy),
            ]
        ).mapped('monto_retenido'))
        sin_ytd_no_recibido = sum(self.env['ve.wh.iva'].search(
            domain_ytd + [
                ('state', '=', 'esperado'),
                '|', ('fecha_vencimiento_entrega', '>', hoy),
                     ('fecha_vencimiento_entrega', '=', False),
            ]
        ).mapped('monto_retenido'))
        sin_ytd_borrador = sum(self.env['ve.wh.iva'].search([
            ('conciliacion_id', 'in', periodos_anio.ids),
            ('state', '=', 'borrador'),
            ('company_id', '=', self.env.company.id),
        ]).mapped('monto_retenido'))
        sin_ytd = sin_ytd_venc + sin_ytd_no_recibido + sin_ytd_borrador
        for rec in self:
            rec.debito_fiscal_cascade = debito_ytd
            rec.retenido_con_comprobante = con_ytd
            rec.retenido_sin_comprobante = sin_ytd
            rec.pct_recuperado = (con_ytd / debito_ytd * 100) if debito_ytd > 0 else 0.0
            rec.pct_en_riesgo = (sin_ytd / debito_ytd * 100) if debito_ytd > 0 else 0.0

            periodo = rec._get_periodo_activo()
            if periodo:
                debito_p = periodo.declaracion_iva_id.campo_49 if periodo.declaracion_iva_id else 0.0
                con_p = sum(self.env['ve.wh.iva'].search([
                    ('conciliacion_id', '=', periodo.id),
                    ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
                ]).mapped('monto_retenido'))
                # Mismas 3 categorías que "Crédito del Período por Estado"
                # (_compute_cobranza_exposicion, conciliacion_id = período
                # activo): no recibido + no confirmado (borrador) + vencido.
                # Se leen directo de esos campos ya computados — un solo
                # cálculo, para que Cascada/Brecha nunca se desincronicen
                # de lo que ya muestra el Operativo.
                sin_p = (rec.estado_no_recibido_bs + rec.estado_borrador_bs
                          + rec.estado_vencido_bs)
                sin_p_count = (rec.estado_no_recibido_count + rec.estado_borrador_count
                                + rec.estado_vencido_count)
                rec.debito_fiscal_periodo = debito_p
                rec.retenido_con_periodo = con_p
                rec.retenido_sin_periodo = sin_p
                rec.retenido_sin_periodo_count = sin_p_count
                rec.pct_recuperado_periodo = (con_p / debito_p * 100) if debito_p > 0 else 0.0
                rec.pct_en_riesgo_periodo = (sin_p / debito_p * 100) if debito_p > 0 else 0.0
            else:
                rec.debito_fiscal_periodo = 0.0
                rec.retenido_con_periodo = 0.0
                rec.retenido_sin_periodo = 0.0
                rec.retenido_sin_periodo_count = 0
                rec.pct_recuperado_periodo = 0.0
                rec.pct_en_riesgo_periodo = 0.0

    # ── Apertura singleton ────────────────────────────────────────────────────
    def _get_or_create_singleton(self):
        dashboard = self.search([], limit=1)
        if not dashboard:
            dashboard = self.sudo().create({'name': 'Dashboard IVA Venezuela'})
        return dashboard

    @api.model
    def action_open_dashboard_operativo(self):
        dashboard = self._get_or_create_singleton()
        view_id = self.env.ref('ve_retencion_iva.ve_dashboard_iva_view_form_operativo').id
        return {
            'type': 'ir.actions.act_window',
            'name': 'Dashboard IVA — Operativo',
            'res_model': 've.dashboard.iva',
            'res_id': dashboard.id,
            'views': [(view_id, 'form')],
            'target': 'current',
            'context': {'form_view_initial_mode': 'readonly'},
        }

    @api.model
    def action_open_dashboard_gerencial(self):
        dashboard = self._get_or_create_singleton()
        view_id = self.env.ref('ve_retencion_iva.ve_dashboard_iva_view_form_gerencial').id
        return {
            'type': 'ir.actions.act_window',
            'name': 'Dashboard IVA — Gerencial',
            'res_model': 've.dashboard.iva',
            'res_id': dashboard.id,
            'views': [(view_id, 'form')],
            'target': 'current',
            'context': {'form_view_initial_mode': 'readonly'},
        }

    # ── Drill-downs ───────────────────────────────────────────────────────────
    def _action_recordatorios_lista(self, name, domain):
        list_view_id = self.env.ref('ve_retencion_iva.ve_wh_iva_view_list_recordatorios').id
        form_view_id = self.env.ref('ve_retencion_iva.ve_wh_iva_view_form').id
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': 've.wh.iva',
            'views': [(list_view_id, 'list'), (form_view_id, 'form')],
            'domain': domain + [('company_id', '=', self.env.company.id)],
        }

    def action_ver_vencidos(self):
        # Mismo dominio que total_vencidos en _compute_semaforo — no
        # duplicar el criterio en otro sitio para no descuadrar botón/lista.
        return self._action_recordatorios_lista(
            'Vencidos — IVA Clientes', [
                ('state', 'in', ('esperado', 'vencido')),
                ('fecha_vencimiento_entrega', '<=', fields.Date.today()),
            ])

    def action_ver_zona_pendiente(self):
        """Drill-down del ranking 'Retenciones Pendientes por Zona' — abre
        Retenciones IVA Clientes filtrado a Esperado+Vencido, agrupado por
        Zona (reusa el filtro/group_by ya existente en la vista de
        búsqueda, ver ve_wh_iva_view_search)."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pendiente por Recuperar — por Zona',
            'res_model': 've.wh.iva',
            'views': [(self.env.ref('ve_retencion_iva.ve_wh_iva_view_list').id, 'list'),
                      (self.env.ref('ve_retencion_iva.ve_wh_iva_view_form').id, 'form')],
            'search_view_id': self.env.ref('ve_retencion_iva.ve_wh_iva_view_search').id,
            'domain': [('state', 'in', ('esperado', 'vencido')),
                       ('company_id', '=', self.env.company.id)],
            'context': {'group_by': ['zona']},
        }

    def _action_aging_lista(self, name, desde, hasta):
        """desde/hasta acotan fecha_vencimiento_entrega (ambos inclusive,
        None = sin límite en ese extremo)."""
        domain = [
            ('state', 'in', ('esperado', 'vencido')),
        ]
        if desde:
            domain.append(('fecha_vencimiento_entrega', '>=', desde))
        if hasta:
            domain.append(('fecha_vencimiento_entrega', '<=', hasta))
        return self._action_recordatorios_lista(name, domain)

    def action_ver_aging_0_15(self):
        hoy = fields.Date.today()
        return self._action_aging_lista(
            'Vencidos 0-15 días — IVA Clientes', hoy - timedelta(days=15), hoy)

    def action_ver_aging_16_30(self):
        hoy = fields.Date.today()
        return self._action_aging_lista(
            'Vencidos 16-30 días — IVA Clientes',
            hoy - timedelta(days=30), hoy - timedelta(days=16))

    def action_ver_aging_31_mas(self):
        hoy = fields.Date.today()
        return self._action_aging_lista(
            'Vencidos +30 días (riesgo crédito fiscal) — IVA Clientes',
            None, hoy - timedelta(days=31))

    def action_ver_esperados(self):
        # Mismo filtro que total_esperados en _compute_semaforo — no
        # duplicar el criterio en otro sitio para no volver a descuadrar
        # botón/lista.
        return self._action_recordatorios_lista(
            'No Recibido — IVA Clientes', [('state', '=', 'esperado')])

    def action_ver_pagado_sin_comprobante(self):
        # Reusa la acción/vistas ya construidas para "Cobranza vs.
        # Comprobante" (ve_wh_iva_cobranza_views.xml, filtro por defecto
        # pagado_sin_comprobante) — no duplicar el dominio aquí.
        return self.env['ir.actions.act_window']._for_xml_id(
            've_retencion_iva.ve_wh_iva_action_cobranza')

    def action_ver_borradores(self):
        periodo = self._get_periodo_activo()
        domain = [('state', '=', 'borrador')]
        if periodo:
            domain.append(('conciliacion_id', '=', periodo.id))
        name = f'No Confirmados — {periodo.periodo_retencion}' if periodo else 'No Confirmados'
        return self._action_recordatorios_lista(name, domain)

    def action_ver_periodos_abiertos(self):
        fecha_corte = fields.Date.today() - relativedelta(years=6)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Períodos Sin Declarar (ventana prescripción 6 años)',
            'res_model': 've.declaracion.iva',
            'view_mode': 'list,form',
            'domain': [
                ('estado_conciliacion_periodo', '!=', 'declarado'),
                ('fecha_fin', '>=', fecha_corte),
                ('company_id', '=', self.env.company.id),
            ],
        }

    def action_ver_declaracion_iva(self):
        """Abre el formulario de Declaración IVA del período de referencia.
        Busca directamente por conciliacion_id para no depender de que
        declaracion_iva_id esté poblado en el período (puede estar vacío en borrador)."""
        if not self.periodo_ref_id:
            return False
        decl = self.env['ve.declaracion.iva'].search(
            [('conciliacion_id', '=', self.periodo_ref_id)], limit=1
        )
        if not decl:
            decl = self.env['ve.declaracion.iva']._get_or_create_for_periodo(
                self.periodo_ref_id)
        return {
            'type': 'ir.actions.act_window',
            'name': 'Declaraci&#xf3;n IVA',
            'res_model': 've.declaracion.iva',
            'res_id': decl.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_ver_retenciones_anio(self):
        today = fields.Date.today()
        year_start, year_end = self._get_rango_ytd()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Retenciones Clientes {today.year}',
            'res_model': 've.wh.iva',
            'view_mode': 'list,graph,form',
            'domain': [
                ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
                ('conciliacion_id.fecha_inicio', '>=', year_start),
                ('conciliacion_id.fecha_inicio', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ],
            'context': {'graph_mode': 'bar', 'graph_measure': 'monto_retenido'},
        }

    def action_ver_retenciones_prov_anio(self):
        """Abre lista de retenciones a proveedores del año."""
        today = fields.Date.today()
        year_start, year_end = self._get_rango_ytd()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Retenciones Proveedores {today.year}',
            'res_model': 've.wh.iva.prov',
            'view_mode': 'list,graph,form',
            'domain': [
                ('declaracion_iva_id.fecha_inicio', '>=', year_start),
                ('declaracion_iva_id.fecha_inicio', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ],
            'context': {'graph_mode': 'bar'},
        }

    def action_ver_periodos_cumplimiento(self):
        year_start, year_end = self._get_rango_ytd()
        today = fields.Date.today()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Puntualidad Fiscal {today.year}',
            'res_model': 've.declaracion.iva',
            'view_mode': 'list,form',
            'domain': [
                ('fecha_fin', '>=', year_start),
                ('fecha_fin', '<=', year_end),
                ('company_id', '=', self.env.company.id),
            ],
            'context': {'order': 'fecha_fin asc'},
        }

    def action_ver_sanciones_anio(self):
        today = fields.Date.today()
        year_start, year_end = self._get_rango_ytd()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Sanciones IVA {today.year}',
            'res_model': 've.sancion.iva',
            'view_mode': 'list,form',
            'domain': [
                ('estado', 'in', ['pendiente', 'impugnada']),
                ('fecha', '>=', str(year_start)),
                ('fecha', '<=', str(year_end)),
            ],
        }

    def action_estimar_riesgo(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Estimar Riesgo SENIAT',
            'res_model': 've.wizard.estimacion.riesgo',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_ver_retenciones_en_riesgo_periodo(self):
        # Mismo dominio que sin_p en _compute_liquidez: no recibido/vencido
        # fuera de plazo, no recibido (aún dentro de plazo) O no confirmado
        # (borrador) del período activo.
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return self._action_recordatorios_lista(
            f'Sin Comprobante — {periodo.periodo_retencion}', [
                ('conciliacion_id', '=', periodo.id),
                ('state', 'in', ['esperado', 'vencido', 'borrador']),
            ])

    def action_ver_retenciones_en_riesgo(self):
        # Mismo dominio que sin_ytd en _compute_liquidez: esperado/vencido
        # (con o sin período asignado, dentro del año) O no confirmado
        # (borrador) del año en curso.
        year_start, year_end = self._get_rango_ytd()
        return self._action_recordatorios_lista(
            'Sin Comprobante (YTD)', [
                '|',
                    '&', ('state', 'in', ['esperado', 'vencido']),
                         '|', ('conciliacion_id', '=', False),
                              '&', ('conciliacion_id.fecha_inicio', '>=', year_start),
                                   ('conciliacion_id.fecha_inicio', '<=', year_end),
                    '&', ('state', '=', 'borrador'),
                         '&', ('conciliacion_id.fecha_inicio', '>=', year_start),
                              ('conciliacion_id.fecha_inicio', '<=', year_end),
            ])

    def action_ver_confirmados_periodo(self):
        # Mismo dominio que "ok" en _compute_checklist (retenciones_ok) —
        # filtra a Confirmados/Conciliados/Declarados del período, para que
        # la lista coincida exactamente con el numerador de la tarjeta.
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return self._action_recordatorios_lista(
            f'Confirmados — {periodo.periodo_retencion}', [
                ('conciliacion_id', '=', periodo.id),
                ('estado_recepcion', 'in', ('confirmado', 'confirmado_dif')),
            ])

    def action_ver_pagado_sin_comprobante_periodo(self):
        # Mismo dominio que sin_comp en _compute_cobranza_exposicion —
        # versión acotada al período activo de action_ver_pagado_sin_comprobante
        # (esa es backlog global, aquí se necesita solo lo que compone el
        # conteo de la tarjeta "# Facturas Pagadas sin Comprobante").
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return self._action_recordatorios_lista(
            f'Pagado sin Comprobante — {periodo.periodo_retencion}', [
                ('conciliacion_id', '=', periodo.id),
                ('state', '!=', 'anulado'),
                ('estado_cobranza', '=', 'pagado_sin_comprobante'),
            ])

    def action_ver_doble_riesgo(self):
        # Mismo dominio que doble_riesgo en _compute_cobranza_exposicion:
        # vencido (por fecha) Y pagado sin comprobante, a la vez.
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return self._action_recordatorios_lista(
            f'Doble Riesgo — {periodo.periodo_retencion}', [
                ('conciliacion_id', '=', periodo.id),
                ('state', 'in', ('esperado', 'vencido')),
                ('fecha_vencimiento_entrega', '<=', fields.Date.today()),
                ('estado_cobranza', '=', 'pagado_sin_comprobante'),
            ])

    # Drill-down por categoría del doughnut "Conciliación de Datos vs.
    # SENIAT" (pedido explícito 2026-07-31) — "Solo SENIAT" queda afuera a
    # propósito: son registros de ve.seniat.retencion que NUNCA se crearon
    # en Odoo (nada que abrir en ve.wh.iva, y esa categoría no tiene zona
    # propia — la zona viaja por account.move/ve.wh.iva, no por el feed de
    # extracción SENIAT).
    _CONCIL_DOMINIOS = {
        'conciliada':     ('Conciliadas', [('estado_conciliacion', 'in', _CONCIL_CONCILIADA_ESTADOS)]),
        'solo_odoo':      ('Solo SmartIVA', [('estado_conciliacion', '=', 'solo_odoo')]),
        'diferencia':     ('Diferencia de Monto', [('estado_conciliacion', '=', 'diferencia')]),
        'sin_conciliar':  ('Sin Conciliar', [('estado_conciliacion', '=', 'pendiente')]),
    }

    def action_ver_concil_categoria(self, categoria, ytd=False):
        """Abre Retenciones IVA Clientes filtrado por la categoría del
        doughnut y agrupado por Zona/Planta — el mecanismo de drill-down
        acordado: no hay clic real sobre el SVG, el botón abre la lista ya
        filtrada/agrupada."""
        info = self._CONCIL_DOMINIOS.get(categoria)
        if not info:
            return False
        label, domain = info
        domain = list(domain) + [('company_id', '=', self.env.company.id)]
        if ytd:
            year_start, year_end = self._get_rango_ytd()
            domain += [('conciliacion_id.fecha_fin', '>=', year_start),
                       ('conciliacion_id.fecha_fin', '<=', year_end)]
            titulo = f'{label} — YTD {fields.Date.today().year}'
        else:
            periodo = self._get_periodo_activo()
            domain += [('conciliacion_id', '=', periodo.id if periodo else False)]
            titulo = f'{label} — {periodo.periodo_retencion if periodo else "Período Activo"}'
        return {
            'type': 'ir.actions.act_window',
            'name': titulo,
            'res_model': 've.wh.iva',
            'views': [(self.env.ref('ve_retencion_iva.ve_wh_iva_view_list').id, 'list'),
                      (self.env.ref('ve_retencion_iva.ve_wh_iva_view_form').id, 'form')],
            'search_view_id': self.env.ref('ve_retencion_iva.ve_wh_iva_view_search').id,
            'domain': domain,
            'context': {'group_by': ['zona']},
        }

    # Wrappers sin argumentos — un botón <button type="object"> de Odoo solo
    # puede llamar un método sin parámetros, no puede pasarle "categoria"/
    # "ytd" desde el XML.
    def action_ver_concil_conciliada(self):
        return self.action_ver_concil_categoria('conciliada')

    def action_ver_concil_solo_odoo(self):
        return self.action_ver_concil_categoria('solo_odoo')

    def action_ver_concil_diferencia(self):
        return self.action_ver_concil_categoria('diferencia')

    def action_ver_concil_sin_conciliar(self):
        return self.action_ver_concil_categoria('sin_conciliar')

    def action_ver_concil_conciliada_ytd(self):
        return self.action_ver_concil_categoria('conciliada', ytd=True)

    def action_ver_concil_solo_odoo_ytd(self):
        return self.action_ver_concil_categoria('solo_odoo', ytd=True)

    def action_ver_concil_diferencia_ytd(self):
        return self.action_ver_concil_categoria('diferencia', ytd=True)

    def action_ver_concil_sin_conciliar_ytd(self):
        return self.action_ver_concil_categoria('sin_conciliar', ytd=True)

    def action_ver_conciliacion_periodo(self):
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': 'Conciliaci&#xf3;n SENIAT',
            'res_model': 've.conciliacion.periodo',
            'res_id': periodo.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_ver_pendientes_confirmar_periodo(self):
        # Mismo dominio que la brecha en _compute_brecha: todo lo que NO
        # es Confirmado/Recibido del período (No Recibido + Recibido sin
        # Confirmar + Vencido).
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return self._action_recordatorios_lista(
            f'Pendientes de Confirmar — {periodo.periodo_retencion}', [
                ('conciliacion_id', '=', periodo.id),
                ('state', 'in', ('esperado', 'vencido', 'borrador')),
            ])

    def action_ver_retenciones_con_comprobante_periodo(self):
        # Mismo dominio que con_p en _compute_liquidez.
        periodo = self._get_periodo_activo()
        if not periodo:
            return False
        return self._action_recordatorios_lista(
            f'Con Comprobante — {periodo.periodo_retencion}', [
                ('conciliacion_id', '=', periodo.id),
                ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
            ])

    def action_ver_retenciones_con_comprobante(self):
        # Mismo dominio que con_ytd en _compute_liquidez.
        year_start, year_end = self._get_rango_ytd()
        return self._action_recordatorios_lista(
            'Con Comprobante (YTD)', [
                '|',
                    ('conciliacion_id', '=', False),
                    '&', ('conciliacion_id.fecha_inicio', '>=', year_start),
                         ('conciliacion_id.fecha_inicio', '<=', year_end),
                ('estado_recepcion', 'in', ['confirmado', 'confirmado_dif']),
            ])
