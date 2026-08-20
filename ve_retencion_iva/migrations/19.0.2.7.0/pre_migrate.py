def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_dashboard_iva
        ADD COLUMN IF NOT EXISTS modo_vista VARCHAR
    """)
    cr.execute("""
        UPDATE ve_dashboard_iva SET modo_vista = 'ytd' WHERE modo_vista IS NULL
    """)
