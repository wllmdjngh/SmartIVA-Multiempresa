import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# Bug real encontrado 2026-09-16 (Vencement, Febrero): una retencion de
# Odoo (ve.wh.iva) que ya llego a estado_conciliacion 'listo_declarar'/
# 'conciliada_norec'/'conciliada'/'aprobado_declarar' queda CONGELADA para
# siempre por _do_conciliar (ve_conciliacion.py, fix 2026-08-18) -- nunca
# se vuelve a re-evaluar, a proposito, para no deshacer un match ya bueno.
# Si el registro SENIAT (ve.seniat.retencion) que origino ese match se
# borra o se reemplaza (ej. volver a correr "Extraer SENIAT" sobre un
# periodo que YA se habia conciliado antes), el dato SENIAT nuevo nunca
# puede volver a encontrar su pareja -- el lado Odoo esta protegido/
# excluido del universo de matching. Queda huerfano como "Solo SENIAT"
# para siempre, aunque el match real exista (mismo RIF + mismo N.Control/
# N.Factura + mismo monto).
#
# Confirmado en vivo: el informe entregado al cliente el 2026-08-22
# mostraba Febrero con 1.225 "Conciliado OK" + 27 "Con Diferencia"; para
# el 2026-09-16 esos mismos periodos mostraban 0 "Conciliado" y 1.416
# "Solo SENIAT".
#
# Clave de match CORRECTA (misma que usa _do_conciliar, no solo RIF+
# Monto): RIF + N.Control normalizado (nivel 1), o RIF + N.Factura
# normalizado si no hay N.Control (nivel 2) -- el Monto se exige exacto
# ADEMAS, nunca en vez de. Un cruce anterior (solo RIF+Monto) encontro
# 314 grupos con el mismo RIF+Monto pero N.Control/N.Factura DISTINTO --
# son transacciones reales distintas que coinciden en monto por
# casualidad (ej. mismo producto, mismo precio, facturas diferentes), no
# duplicados -- por eso la clave real debe incluir N.Control/N.Factura
# SIEMPRE.
#
# Este fix tiene 2 partes:
# 1. Deduplicar filas ve.seniat.retencion EXACTAS que ya existan (mismo
#    RIF+Control/Factura+Monto, sin importar estado) -- si dos candidatos
#    identicos compiten por la misma retencion de Odoo, _do_conciliar no
#    adivina, se rinde (deja la retencion en 'solo_odoo', PEOR que como
#    estaba si venia de 'diferencia' -- perdia hasta el diagnostico que
#    ya tenia). Se conserva la fila 'conciliado' si alguna lo esta,
#    si no la de menor id (la mas antigua).
# 2. Descongelar SOLO los candidatos SIN AMBIGUEDAD (exactamente 1 wh.iva
#    coincide por la clave real) y volver a conciliar los periodos
#    afectados. Cualquier caso con 0 o 2+ candidatos se deja intacto y
#    se registra en el log para revision manual -- no se adivina.


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    WhIva = env['ve.wh.iva']
    Seniat = env['ve.seniat.retencion']
    Periodo = env['ve.conciliacion.periodo']
    Conc = env['ve.conciliacion.periodo']
    norm_rif = Conc._norm_rif
    norm_ctrl = Conc._norm_ctrl
    norm_factura = Conc._norm_factura

    def clave_seniat(rec):
        rn = norm_rif(rec.rif_agente)
        if rec.nro_control:
            return (rn, 'ctrl', norm_ctrl(rec.nro_control))
        if rec.nro_documento:
            return (rn, 'fact', norm_factura(rec.nro_documento))
        return None

    # ── Parte 1: deduplicar filas SENIAT exactas ─────────────────────────
    no_conciliadas = Seniat.search([('estado', '!=', 'conciliado')])
    grupos = {}
    for r in no_conciliadas:
        k = clave_seniat(r)
        if k:
            grupos.setdefault((k, round(r.monto_retenido, 2)), []).append(r)

    a_borrar_ids = []
    for (k, monto), recs in grupos.items():
        if len(recs) <= 1:
            continue
        recs_sorted = sorted(recs, key=lambda r: r.id)
        a_borrar_ids += [r.id for r in recs_sorted[1:]]

    _logger.info(
        've_retencion_iva 19.0.2.14.229: %d fila(s) SENIAT duplicada(s) '
        'exacta(s) a eliminar', len(a_borrar_ids))
    if a_borrar_ids:
        Seniat.browse(a_borrar_ids).unlink()

    # ── Parte 2: descongelar candidatos SIN ambiguedad ───────────────────
    sin_match = Seniat.search([('estado', '=', 'sin_match')])
    _logger.info(
        've_retencion_iva 19.0.2.14.229: %d registro(s) SENIAT sin_match a revisar',
        len(sin_match))

    avanzadas = WhIva.search([
        ('estado_conciliacion', 'in', [
            'listo_declarar', 'conciliada_norec', 'conciliada', 'aprobado_declarar']),
    ])
    by_key = {}
    for w in avanzadas:
        rn = norm_rif(w.rif)
        monto = round(w.monto_retenido, 2)
        if w.nro_control:
            by_key.setdefault((rn, 'ctrl', norm_ctrl(w.nro_control), monto), []).append(w.id)
        factura_raw = w.invoice_id.name if w.invoice_id else (w.nro_documento or '')
        if factura_raw:
            by_key.setdefault((rn, 'fact', norm_factura(factura_raw), monto), []).append(w.id)

    candidatos = set()
    ambiguos = 0
    periodos_a_reconciliar = set()
    for r in sin_match:
        rn = norm_rif(r.rif_agente)
        monto = round(r.monto_retenido, 2)
        wids = None
        if r.nro_control:
            wids = by_key.get((rn, 'ctrl', norm_ctrl(r.nro_control), monto))
        if not wids and r.nro_documento:
            wids = by_key.get((rn, 'fact', norm_factura(r.nro_documento), monto))
        if not wids:
            continue
        wids_unicos = set(wids)
        if len(wids_unicos) > 1:
            ambiguos += 1
            continue
        candidatos.update(wids_unicos)
        if r.conciliacion_id:
            periodos_a_reconciliar.add(r.conciliacion_id.id)

    _logger.info(
        've_retencion_iva 19.0.2.14.229: %d wh.iva candidato(s) sin ambiguedad a '
        'descongelar, %d caso(s) ambiguo(s) dejado(s) intacto(s) (revisar manual), '
        '%d periodo(s) a re-conciliar',
        len(candidatos), ambiguos, len(periodos_a_reconciliar))

    if candidatos:
        WhIva.browse(list(candidatos)).write({
            'estado_conciliacion': 'pendiente',
            'monto_seniat': 0,
            'fecha_conciliacion': False,
            'nivel_match': False,
            'matched_por_normalizacion': False,
        })

    for p in Periodo.browse(list(periodos_a_reconciliar)):
        try:
            # _do_conciliar() directo, no el wrapper publico action_conciliar()
            # -- ese wrapper devuelve un wizard de confirmacion en vez de
            # conciliar de una vez si el periodo tiene wh_iva_ids en estado
            # 'borrador' (ver ve_conciliacion.py::action_conciliar), y una
            # migracion no puede interactuar con ningun wizard.
            p._do_conciliar()
        except Exception:
            _logger.exception(
                've_retencion_iva 19.0.2.14.229: fallo al conciliar periodo %s',
                p.periodo_retencion)

    _logger.info('ve_retencion_iva 19.0.2.14.229: terminado')
