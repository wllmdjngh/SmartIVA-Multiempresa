import logging

_logger = logging.getLogger(__name__)

# Campo nuevo ve.conciliacion.periodo.monto_conciliado_seniat -- ver
# models/ve_conciliacion.py. Pedido explicito 2026-08-18: la vista list
# comparaba "Conciliado" (monto_retenido esperado) contra "SENIAT" (total
# de la fila, homed por periodo_retencion) -- con el match por universo de
# compania, ambas columnas ya no tenian por que cuadrar entre si y
# confundia. Se reemplaza por una sola columna que suma monto_seniat real
# de las conciliadas del periodo.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS monto_conciliado_seniat numeric
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.107: columna monto_conciliado_seniat '
        'asegurada en ve_conciliacion_periodo')
