import logging

_logger = logging.getLogger(__name__)

# Campos nuevos ve.conciliacion.periodo.monto_conciliado_fuera_periodo /
# monto_conciliado_diferencia / monto_sin_match_seniat -- ver
# models/ve_conciliacion.py. Pedido explicito 2026-08-18: descomponer
# total_seniat de cada periodo en 4 partes que siempre suman exacto
# (conciliado del periodo / fuera de periodo / con diferencia / sin
# match), ademas del fix real en _do_conciliar (dejaba de resetear
# retenciones ya matcheadas limpio, rompiendo 9.731 de 9.732 matches
# buenos en cada corrida).


def migrate(cr, version):
    for col in (
        "monto_conciliado_fuera_periodo",
        "monto_conciliado_diferencia",
        "monto_sin_match_seniat",
    ):
        cr.execute(f"""
            ALTER TABLE ve_conciliacion_periodo ADD COLUMN IF NOT EXISTS {col} numeric
        """)
    _logger.info(
        've_retencion_iva 19.0.2.14.109: columnas de desglose SENIAT '
        'aseguradas en ve_conciliacion_periodo')
