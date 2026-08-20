import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Backfill del campo nuevo nro_control_repetido (ver
    # models/ve_seniat_retencion.py) -- create()/write() lo mantienen al
    # día de ahora en adelante, pero los ~18.000 registros históricos
    # nunca pasaron por ese código. SQL directo por volumen (una sola
    # pasada, agrupado por company_id -- correcto incluso con varias
    # compañías en la misma base, cada grupo ya queda separado por su
    # propio company_id en el GROUP BY).
    # COALESCE(nro_documento, '') -- COUNT(DISTINCT x) de Postgres ignora
    # NULL por completo; sin el coalesce, un grupo con algunas filas de
    # nro_documento NULL y otras con valor real podía no contarse como
    # "repetido" pese a tener valores distintos de verdad.
    cr.execute("""
        UPDATE ve_seniat_retencion s
        SET nro_control_repetido = true
        FROM (
            SELECT nro_control, rif_agente, company_id
            FROM ve_seniat_retencion
            WHERE nro_control IS NOT NULL AND nro_control != ''
            GROUP BY nro_control, rif_agente, company_id
            HAVING COUNT(DISTINCT COALESCE(nro_documento, '')) > 1
        ) grp
        WHERE s.nro_control = grp.nro_control
          AND s.rif_agente = grp.rif_agente
          AND s.company_id IS NOT DISTINCT FROM grp.company_id
    """)
    _logger.info(
        've_retencion_iva %s: nro_control_repetido backfilled, %d fila(s) marcadas',
        version, cr.rowcount,
    )
