def berechne_steuer(brutto, steuersatz, konto_typ):
    if steuersatz > 0:
        netto = brutto / (1 + steuersatz / 100)
        steuer = brutto - netto
    else:
        netto, steuer = brutto, 0.0

    steuer_abs = round(abs(steuer), 2)

    if konto_typ == "ausgabe":
        steuerbetrag = steuer_abs
    else:
        steuerbetrag = -steuer_abs

    return round(netto, 2), steuerbetrag