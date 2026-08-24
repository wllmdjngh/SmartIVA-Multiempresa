import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Reintento del backfill de 19.0.2.14.113 -- verificado por RPC que
    # sigue en 335 filas stale tras ese deploy (no se pudo confirmar por qué
    # no corrió: puede que Odoo.sh no haya ejecutado -u para esa versión
    # puntual). Idempotente -- si 19.0.2.14.113 SÍ corrió en algún momento
    # entre medio, este UPDATE simplemente no encuentra filas que tocar.
    cr.execute("""
        UPDATE ve_conecta_carga_ventas_linea
        SET categoria_discrepancia = 'registro_anulacion'
        WHERE es_anulacion_par = true
          AND (categoria_discrepancia IS NULL OR categoria_discrepancia = '')
    """)
    _logger.info(
        've_retencion_iva %s: categoria_discrepancia backfilled (reintento), '
        '%d fila(s) corregidas',
        version, cr.rowcount,
    )
