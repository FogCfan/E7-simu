# -*- coding: utf-8 -*-
# MHU (Arm MHUv2) fonctionnel de E7 Twin (agent 04). Un periphérique Python par LIEN : il porte les deux
# fenêtres locales (TX côté cœur A, RX côté cœur B), donc l'état est partagé entre les deux cœurs.
# Parametres definis avant ce corps par e7twin/renode/stubs.py :
#   LINK      : nom du lien (ex. 'm55hp_m55he_0')
#   ENDPOINTS : [{'role': 'tx'|'rx', 'base': int, 'core': 'm55_hp', 'nvic': 'nvic_hp', 'irq': int}, ...]
#   PEER      : {'tx': core source ou None, 'rx': core destination ou None}
# Registres : pack Alif 2.2.1, drivers/include/mhu_driver.h (MHU_sender_frame_register_t /
# MHU_receiver_frame_register_t), comportement : drivers/source/mhu_sender.c et mhu_receiver.c.
# Chaque message est journalise : "MHU_EVT t_us=.. link=.. kind=send|ack src=.. dst=.. ch=.. val=0x.."

FRAME = 0xF80            # 124 canaux * 0x20 : registres de trame
NCH = 124

if request.IsInit:
    _st = {}                       # canal -> CH_ST (partage TX/RX)
    _mask = {}                     # canal -> masque RX (CH_MASK_ST)
    _req = 0                       # TX ACCESS_REQUEST
    _txien = 0                     # TX INT_EN
    _nr2r = 0
    _r2nr = 0
    _ch_int_st = {}                # TX CH_INT_ST par canal
    _ch_int_en = {}                # TX CH_INT_EN par canal
    _rxien = 0                     # RX INT_EN
    _resp = 0
    _level = {}
    _seen = set()
else:
    a = request.Absolute
    ep = None
    for e in ENDPOINTS:
        if e['base'] <= a < e['base'] + 0x1000:
            ep = e
    off = a - ep['base']
    tx = ep['role'] == 'tx'

    def _now():
        return int(self.GetMachine().LocalTimeSource.ElapsedVirtualTime.TotalMicroseconds)

    def _evt(kind, src, dst, ch, val):
        self.InfoLog("MHU_EVT t_us=%d link=%s kind=%s src=%s dst=%s ch=%d val=0x%X" % (
            _now(), LINK, kind, src, dst, ch, val))

    def _rx_pending():
        return [n for n in _st if _st[n] & ~_mask.get(n, 0)]

    def _chcomb_tx():
        return [n for n in _ch_int_st if _ch_int_st[n] & _ch_int_en.get(n, 0)]

    def _tx_int_st():
        return ((_nr2r & 1) | ((_r2nr & 1) << 1) | ((1 if _chcomb_tx() else 0) << 2)) & _txien

    def _rx_int_st():
        return ((1 if _rx_pending() else 0) << 2) & _rxien

    def _update_irqs():
        for e in ENDPOINTS:
            lvl = bool(_tx_int_st()) if e['role'] == 'tx' else bool(_rx_int_st())
            key = (e['core'], e['role'])
            if _level.get(key, False) != lvl:
                _level[key] = lvl
                self.GetMachine()['sysbus.' + e['nvic']].OnGPIO(e['irq'], lvl)

    def _group(chs, g):
        v = 0
        for n in chs:
            if n // 32 == g:
                v |= 1 << (n % 32)
        return v

    key = (request.IsRead, off)
    if key not in _seen and not (off < FRAME and (off & 0x1F) in (0x0, 0x4, 0x8, 0xC, 0x10, 0x14, 0x18)):
        _seen.add(key)
        self.WarningLog("registre MHU non modelise (lien %s, %s) : %s offset 0x%X" % (
            LINK, ep['role'], "lecture" if request.IsRead else "ecriture", off))

    if off < FRAME:
        ch, reg = off // 0x20, off & 0x1F
        if request.IsRead:
            v = 0
            if reg == 0x00:                                   # CH_ST (TX et RX)
                v = _st.get(ch, 0)
            elif tx and reg == 0x10:                          # TX CH_INT_ST
                v = _ch_int_st.get(ch, 0) & _ch_int_en.get(ch, 0)
            elif tx and reg == 0x18:                          # TX CH_INT_EN
                v = _ch_int_en.get(ch, 0)
            elif (not tx) and reg == 0x04:                    # RX CH_ST_MASKED
                v = _st.get(ch, 0) & ~_mask.get(ch, 0)
            elif (not tx) and reg == 0x10:                    # RX CH_MASK_ST
                v = _mask.get(ch, 0)
            request.Value = v & 0xFFFFFFFF
        else:
            val = request.Value
            if tx and reg == 0x0C:                            # TX CH_SET : le message
                _st[ch] = (_st.get(ch, 0) | val) & 0xFFFFFFFF
                _evt("send", PEER['tx'], PEER['rx'] or 'none', ch, val)
            elif tx and reg == 0x14:                          # TX CH_INT_CLR
                _ch_int_st[ch] = _ch_int_st.get(ch, 0) & ~val
            elif tx and reg == 0x18:                          # TX CH_INT_EN
                _ch_int_en[ch] = val & 1
            elif (not tx) and reg == 0x08:                    # RX CH_CLR : acquittement
                before = _st.get(ch, 0)
                _st[ch] = before & ~val
                if before and _st[ch] == 0:
                    _ch_int_st[ch] = 1                        # le TX voit son canal acquitte
                    _evt("ack", PEER['rx'], PEER['tx'] or 'none', ch, before)
            elif (not tx) and reg == 0x14:                    # RX CH_MSK_SET
                _mask[ch] = _mask.get(ch, 0) | val
            elif (not tx) and reg == 0x18:                    # RX CH_MSK_CLR
                _mask[ch] = _mask.get(ch, 0) & ~val
    else:
        r = off - FRAME
        if request.IsRead:
            v = 0
            if r == 0x00:                                     # MHU_CFG : nombre de canaux
                v = NCH
            elif tx and r == 0x04:
                v = _resp
            elif tx and r == 0x08:
                v = _req
            elif tx and r == 0x0C:                            # ACCESS_READY : le recepteur accorde aussitot
                v = _req & 1
            elif r == 0x10:                                   # INT_ST
                v = _tx_int_st() if tx else _rx_int_st()
            elif r == 0x18:                                   # INT_EN
                v = _txien if tx else _rxien
            elif 0x20 <= r <= 0x2C:                           # CH_INT_ST0..3 (groupes de 32 canaux)
                g = (r - 0x20) // 4
                v = _group(_chcomb_tx() if tx else _rx_pending(), g)
            request.Value = v & 0xFFFFFFFF
        else:
            val = request.Value
            if tx and r == 0x04:
                _resp = val
            elif tx and r == 0x08:                            # ACCESS_REQUEST
                new = val & 1
                if new and not _req:
                    _nr2r = 1
                elif _req and not new:
                    _r2nr = 1
                _req = new
            elif r == 0x14:                                   # INT_CLR
                if tx:
                    if val & 1:
                        _nr2r = 0
                    if val & 2:
                        _r2nr = 0
            elif r == 0x18:                                   # INT_EN
                if tx:
                    _txien = val & 7
                else:
                    _rxien = val & 7
        _update_irqs()
    _update_irqs()
