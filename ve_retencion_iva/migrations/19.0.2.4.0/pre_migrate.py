def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_conciliacion_periodo
        ADD COLUMN IF NOT EXISTS total_no_recibido_prev NUMERIC
    """)
