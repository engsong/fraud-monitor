import math

cx, cy = 0.50, 0.50

stations_plan = [
    # --- CROSS BORDER GATEWAYS (4 Corners + Top) ---
    ('UPI', 142, 'outer_far', '#ff9900', 'cross', 'UNIONPAY (CN)'),
    ('e-CNY', 94, 'outer_far', '#ff9900', 'cross', 'e-CNY (CN)'),
    ('NAPAS', 38, 'outer_far', '#ff9900', 'cross', 'NAPAS (VN)'),
    ('BAKONG', 322, 'outer_far', '#ff9900', 'cross', 'BAKONG (KH)'),
    ('ITMX', 218, 'outer_far', '#ff9900', 'cross', 'ITMX (TH)'),

    # --- NORTH SECTOR ---
    ('APB', 82, 'inner', '#4dd0e1', 'banks', 'APB BANK'),
    ('KBANK', 72, 'outer', '#80d8ff', 'banks', 'KASIKORN'),
    ('LFX', 62, 'inner', '#69f0ae', 'wallets', 'LAO FX'),
    ('LBB', 50, 'outer', '#80d8ff', 'banks', 'LBB CHINA'),
    ('ICBC', 106, 'outer', '#80d8ff', 'banks', 'ICBC BANK'),
    ('BOC', 118, 'inner', '#80d8ff', 'banks', 'BOC CHINA'),
    ('LDB', 128, 'outer', '#26c6da', 'banks', 'LDB NORTH'),
    ('FINPASS', 136, 'inner', '#69f0ae', 'wallets', 'FINPASS'),

    # --- EAST SECTOR ---
    ('FINA', 28, 'inner', '#69f0ae', 'wallets', 'FINA'),
    ('STB', 18, 'outer', '#80deea', 'banks', 'STB BANK'),
    ('PSV', 9, 'inner', '#4facfe', 'banks', 'PSV BANK'),
    ('LVB', 0, 'outer', '#00c6ff', 'banks', 'LVB BANK'),
    ('SACOM', 351, 'inner', '#82b1ff', 'banks', 'SACOMBANK'),
    ('VTB', 342, 'outer', '#82b1ff', 'banks', 'MB LAOS'),
    ('FINLINK', 332, 'inner', '#69f0ae', 'wallets', 'FINLINK'),

    # --- SOUTH SECTOR ---
    ('VCBLAO', 312, 'outer', '#82b1ff', 'banks', 'VIETCOMBANK'),
    ('ACLE', 302, 'inner', '#00b0ff', 'banks', 'ACLEDA'),
    ('MJB', 292, 'outer', '#00bfa5', 'banks', 'MJB BANK'),
    ('IDB', 282, 'inner', '#29b6f6', 'banks', 'IDB BANK'),
    ('BIC', 272, 'outer', '#26a69a', 'banks', 'BIC BANK'),
    ('UMONEY', 264, 'inner', '#00e676', 'wallets', 'U-MONEY'),
    ('VMB', 254, 'outer', '#26c6da', 'banks', 'VIETINBANK'),
    ('LOCAPAY', 244, 'inner', '#69f0ae', 'wallets', 'LOCAPAY'),
    ('MMONEY', 234, 'outer', '#00e676', 'wallets', 'M-MONEY'),
    ('EASYTAX', 226, 'inner', '#ffd700', 'gov', 'EASYTAX'),

    # --- WEST SECTOR ---
    ('LNSW', 208, 'outer', '#ffd700', 'gov', 'LNSW'),
    ('MOFPAY', 198, 'inner', '#ffd700', 'gov', 'MOFPAY'),
    ('PBLL', 188, 'outer', '#80d8ff', 'banks', 'PUBLIC BANK'),
    ('BCEL', 178, 'inner', '#00f2fe', 'banks', 'BCEL HQ'),
    ('LCNB', 168, 'outer', '#26c6da', 'banks', 'LCNB BANK'),
    ('JDB', 158, 'inner', '#00e5ff', 'banks', 'JDB BANK'),
    ('BFL', 150, 'outer', '#26c6da', 'banks', 'BFL BRED')
]

angles = [s[1] for s in stations_plan]
print(f"Total stations: {len(stations_plan)}")
print(f"Unique angles: {len(set(angles))}")
assert len(stations_plan) == len(set(angles)), "Duplicate angle detected!"

# Sort by angle ascending
stations_plan.sort(key=lambda s: s[1])

print("\n=== GENERATED NON-OVERLAPPING STATIONS JS ===")
for code, deg, tier, color, cat, label in stations_plan:
    rad = math.radians(deg)
    if tier == 'inner':
        rx, ry = 0.22, 0.24
    elif tier == 'outer':
        rx, ry = 0.38, 0.38
    else: # outer_far (gateways)
        rx, ry = 0.44, 0.42
    
    x = round(cx + rx * math.cos(rad), 3)
    y = round(cy - ry * math.sin(rad), 3)
    print(f'      "{code}": {{ x: {x:.3f}, y: {y:.3f}, angle: {deg}, label: "{label}", color: "{color}", category: "{cat}", tier: "{tier}" }},')
