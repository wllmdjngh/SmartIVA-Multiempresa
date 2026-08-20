import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Crea ve.declaracion.iva para todos los períodos existentes y los vincula."""

    # Períodos que aún no tienen declaración
    cr.execute("""
        SELECT
            p.id,
            p.periodo_retencion,
            p.fecha_inicio,
            p.fecha_fin,
            COALESCE(p.campo_33_arrastre, 0.0) AS campo_33_arrastre,
            COALESCE(p.campo_20, 0.0)          AS campo_20,
            p.estado,
            p.nro_declaracion,
            p.fecha_declaracion,
            p.declarado_por_id,
            COALESCE(p.declarado_por_rpa, false) AS declarado_por_rpa,
            COALESCE(p.estado_prov, 'pendiente') AS estado_prov,
            p.nro_declaracion_prov,
            p.fecha_declaracion_prov,
            p.declarado_prov_por_id,
            p.create_uid,
            p.write_uid
        FROM ve_conciliacion_periodo p
        WHERE p.declaracion_iva_id IS NULL
        ORDER BY p.periodo_retencion ASC
    """)
    periodos = cr.fetchall()

    if not periodos:
        _logger.info('ve_retencion_iva 19.0.2.9.0 post_migrate: sin períodos pendientes, nada que migrar.')
        return

    migrados = 0
    for row in periodos:
        (pid, periodo_ret, fecha_ini, fecha_fin,
         c33_arr, c20, estado, nro_decl, fecha_decl, decl_por, decl_rpa,
         estado_prov, nro_prov, fecha_prov, decl_prov_por,
         create_uid, write_uid) = row

        # Estado de la declaración: si el período ya fue declarado → presentada
        estado_decl = 'presentada' if estado == 'declarado' else 'borrador'
        name = f'DECL-IVA/{periodo_ret}' if periodo_ret else f'DECL-IVA/{pid}'

        cr.execute("""
            INSERT INTO ve_declaracion_iva (
                conciliacion_id,
                name,
                periodo_retencion,
                fecha_inicio,
                fecha_fin,
                campo_33_arrastre,
                campo_20,
                estado,
                nro_declaracion,
                fecha_declaracion,
                declarado_por_id,
                declarado_por_rpa,
                estado_prov,
                nro_declaracion_prov,
                fecha_declaracion_prov,
                declarado_prov_por_id,
                create_uid,
                write_uid,
                create_date,
                write_date
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, NOW(), NOW()
            )
            RETURNING id
        """, (
            pid, name, periodo_ret, fecha_ini, fecha_fin,
            c33_arr, c20, estado_decl,
            nro_decl, fecha_decl, decl_por, decl_rpa,
            estado_prov, nro_prov, fecha_prov, decl_prov_por,
            create_uid or 1, write_uid or 1,
        ))
        decl_id = cr.fetchone()[0]

        cr.execute("""
            UPDATE ve_conciliacion_periodo
            SET declaracion_iva_id = %s
            WHERE id = %s
        """, (decl_id, pid))

        migrados += 1

    _logger.info(
        've_retencion_iva 19.0.2.9.0 post_migrate: %d período(s) migrados a ve.declaracion.iva.',
        migrados,
    )

    # Migrar ve_wh_iva_prov.periodo_id → declaracion_iva_id
    cr.execute("""
        UPDATE ve_wh_iva_prov p
        SET declaracion_iva_id = d.id
        FROM ve_declaracion_iva d
        WHERE d.conciliacion_id = p.periodo_id
          AND p.declaracion_iva_id IS NULL
          AND p.periodo_id IS NOT NULL
    """)
    rows = cr.rowcount
    _logger.info(
        've_retencion_iva 19.0.2.9.0 post_migrate: %d comprobantes prov. vinculados a ve.declaracion.iva.',
        rows,
    )
