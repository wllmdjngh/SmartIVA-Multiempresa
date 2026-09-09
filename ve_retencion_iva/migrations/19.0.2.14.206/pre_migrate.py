import logging

_logger = logging.getLogger(__name__)

# Campos nuevos ve.conecta.carga.ventas.mapeo_manual/headers_no_reconocidos
# (A1, 2026-09-09) -- mapeo manual de columnas del Libro de Ventas no
# reconocidas automáticamente. Ver models/ve_conecta_carga_ventas.py.
#
# Campo nuevo ve.conecta.carga.ventas.linea.es_anulacion (A2, 2026-09-09) --
# señal independiente de tipo_transaccion, token "ANU" del código real del
# cliente (ej. "00-ANU"/"03-ANU-NC"). Ver _es_anulacion_tipo_transaccion.


def migrate(cr, version):
    for columna in ('mapeo_manual', 'headers_no_reconocidos'):
        cr.execute(f"""
            ALTER TABLE ve_conecta_carga_ventas ADD COLUMN IF NOT EXISTS {columna} text
        """)
    cr.execute("""
        ALTER TABLE ve_conecta_carga_ventas_linea
        ADD COLUMN IF NOT EXISTS es_anulacion boolean DEFAULT false
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.206: columnas mapeo_manual, '
        'headers_no_reconocidos aseguradas en ve_conecta_carga_ventas; '
        'es_anulacion asegurada en ve_conecta_carga_ventas_linea')
