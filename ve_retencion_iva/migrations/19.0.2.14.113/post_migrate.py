import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Backfill de categoria_discrepancia='registro_anulacion' para pares
    # Registro+Anulación ya resueltos (es_anulacion_par=true) cuyo compute
    # almacenado quedó stale en False -- encontrado 2026-08-21 al investigar
    # por qué la conciliación Libro de Ventas vs SmartIVA mostraba 66 "c/DIF"
    # falsos (Vencement: 335 de 438 pares con la etiqueta vieja). No es un
    # problema de datos financieros -- es_anulacion_par ya era correcto,
    # la Nota de Crédito real ya existía, sin bloqueante -- solo esta
    # columna de clasificación (usada para filtrar la pestaña
    # "Discrepancias" de la carga) no se había refrescado. Un Upgrade del
    # módulo NO recalcula stored compute ya poblados (Odoo solo los computa
    # si están NULL), de ahí la necesidad de este backfill explícito.
    cr.execute("""
        UPDATE ve_conecta_carga_ventas_linea
        SET categoria_discrepancia = 'registro_anulacion'
        WHERE es_anulacion_par = true
          AND (categoria_discrepancia IS NULL OR categoria_discrepancia = '')
    """)
    _logger.info(
        've_retencion_iva %s: categoria_discrepancia backfilled para pares '
        'Registro+Anulación stale, %d fila(s) corregidas',
        version, cr.rowcount,
    )
