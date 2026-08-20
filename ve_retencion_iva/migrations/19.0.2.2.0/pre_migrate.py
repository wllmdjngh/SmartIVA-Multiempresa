def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS planilla_declaracion_prov BYTEA,
        ADD COLUMN IF NOT EXISTS planilla_declaracion_prov_nombre VARCHAR
    """)
