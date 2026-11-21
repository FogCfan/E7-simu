# Périphérique Python Renode de E7 Twin (agent 04).
# Chargé via `script:` inline généré par e7twin/renode/stubs.py, qui définit avant d'exécuter ce fichier :
#   KIND  : 'regfile' | 'counter' | 'uartdw'
#   BLOCK : nom du bloc (clé du modèle de carte)
#   READY : {offset: masque OR-é en lecture}  (bits "ready/locked" que le matériel lèverait seul)
#   FREQ, ROLE : pour KIND == 'counter' (ROLE 'control' | 'read')
# Politique « pas de zéro silencieux » : le 1er accès (lecture ou écriture) à chaque offset est
# journalisé en WARNING, avec le PC du cœur ; la lecture renvoie la dernière valeur écrite.
# FIDELITE.md : tous ces blocs sont des approximations (aucun effet de bord matériel).

if request.IsInit:
    _regs = {}
    _seen = set()
else:
    off = request.Offset
    if KIND == 'counter':
        us = self.GetMachine().LocalTimeSource.ElapsedVirtualTime.TotalMicroseconds
        ticks = int(us * FREQ / 1000000.0)
        # contrôle : CNTCR(0) CNTSR(4) CNTCVL(8) CNTCVH(0xC) ; lecture : CNTCVL(0) CNTCVH(4)
        base = 8 if ROLE == 'control' else 0
        if request.IsRead:
            if off == base:
                request.Value = ticks & 0xFFFFFFFF
            elif off == base + 4:
                request.Value = (ticks >> 32) & 0xFFFFFFFF
            elif ROLE == 'control' and off == 4:
                request.Value = (_regs.get(0, 0) & 1)       # CNTSR : reflète CNTCR.EN (approx.)
            else:
                request.Value = _regs.get(off, 0)
        else:
            _regs[off] = request.Value
    elif KIND == 'uartdw':
        # registres DesignWare APB UART au-dela du NS16550 de Renode (offset absolu = WINDOW + offset)
        a = WINDOW + off
        key = (request.IsRead, a)
        if key not in _seen:
            _seen.add(key)
            self.WarningLog("registre DW UART approxime (stub %s) : %s offset 0x%X" % (
                BLOCK, "lecture" if request.IsRead else "ecriture", a))
        if request.IsRead:
            if a == 0x7C:      # USR : TFNF (bit1) | TFE (bit2) ; BUSY=0 ; RX non modelise (RFNE=0)
                request.Value = 0x06
            else:              # TFL, RFL (0 = FIFO vides), le reste : derniere valeur ecrite
                request.Value = _regs.get(a, 0)
        else:
            _regs[a] = request.Value
    else:
        key = (request.IsRead, off)
        if key not in _seen:
            _seen.add(key)
            self.WarningLog("accès non modélisé (stub %s) : %s offset 0x%X%s" % (
                BLOCK, "lecture" if request.IsRead else "écriture", off,
                "" if request.IsRead else " valeur 0x%X" % request.Value))
        if request.IsRead:
            request.Value = _regs.get(off, 0) | READY.get(off, 0)
        else:
            _regs[off] = request.Value
