import logging

_logger = logging.getLogger(__name__)

# Fix real de A1 (2026-09-09, encontrado probando en Multiempresa): el
# menu izquierdo de Mapeo Manual de Columnas (header_original) no
# mostraba nada -- un Selection dinamico (selection=metodo) se evalua UNA
# SOLA VEZ con un recordset VACIO al cargar la vista (fields_get()), no
# por cada fila como un dominio de Many2one. Se cambia header_original de
# Selection a Many2one a un modelo nuevo (uno de los que representa cada
# columna sin reconocer), filtrado por dominio carga_id -- eso si se
# evalua por fila en tiempo real. Ver models/ve_conecta_carga_ventas.py.


def migrate(cr, version):
    cr.execute("""
        CREATE TABLE IF NOT EXISTS ve_conecta_carga_ventas_header_no_reconocido (
            id serial PRIMARY KEY,
            carga_id integer,
            nombre varchar,
            create_uid integer,
            create_date timestamp,
            write_uid integer,
            write_date timestamp
        )
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.208: tabla '
        've_conecta_carga_ventas_header_no_reconocido asegurada')
