import serial
import time

# ── Configuration ─────────────────────────────────────────────────────────────
PORT = 'COM6'
BAUD = 9600

# Pumps (DT ASCII)
AQUEOUS    = ['1', '2', '3']
ORGANIC    = ['4']
ALL_PUMPS  = AQUEOUS + ORGANIC

INTAKE_PORT = {'1': 1, '2': 1, '3': 1, '4': 12}   # pump internal valve intake
OUTPUT_PORT = 8                                    # pump internal valve output

ASPIRATE_SPEED = 4     # S code (higher = slower)
DISPENSE_SPEED = 16

# SV-07 switch valve (Runze binary)
VALVE_ADDR      = 0x01
NUM_VALVE_PORTS = 9

# Syringe calibration
SYRINGE_FULL_STEPS = 12000
SYRINGE_VOLUME_ML  = 5.0
def ml_to_steps(ml):
    return int(round(ml / SYRINGE_VOLUME_ML * SYRINGE_FULL_STEPS))

VOLUME_ML = 2.0
STEPS = ml_to_steps(VOLUME_ML)

# ── Serial connection (shared by both protocols) ──────────────────────────────
ser = serial.Serial(PORT, baudrate=BAUD, bytesize=8, parity='N', stopbits=1,
                    timeout=2, rtscts=False, xonxoff=False)
ser.setRTS(True); ser.setDTR(True)
time.sleep(0.5)

# ══ PUMPS: DT ASCII ════════════════════════════════════════════════════════════
def pump_send(addr, cmd, wait=0.15, read=True, label=""):
    ser.reset_input_buffer()
    ser.write(f'/{addr}{cmd}\r'.encode())
    time.sleep(wait)
    resp = ser.read_all() if read else b''
    if label:
        print(f"  [pump {addr} {label}] <- {resp}")
    return resp

def pump_status(addr):
    return pump_send(addr, 'Q', wait=0.1)

def pump_is_busy(addr):
    r = pump_status(addr)
    if not r or len(r) < 3:
        return True
    return not (r[2] & 0x20)

def pump_move_valve(addr, port):
    pump_send(addr, f'I{port}R', wait=0.15, read=False)
    print(f"  -> pump {addr}: internal valve to port {port}")

def fire_all(addrs, cmd):
    for a in addrs:
        pump_send(a, cmd, wait=0.05, read=False)
        print(f"  -> pump {a}: {cmd}")

def wait_all_idle(addrs, timeout=120, poll=0.4, what=""):
    t0 = time.time()
    pending = set(addrs)
    while pending and (time.time() - t0) < timeout:
        for a in list(pending):
            if not pump_is_busy(a):
                pending.discard(a)
                print(f"  [ok] pump {a} idle  (t+{time.time()-t0:4.1f}s)")
        time.sleep(poll)
    if pending:
        print(f"  [!] timeout{(' '+what) if what else ''} -- still busy: {sorted(pending)}")
    return not pending

# ══ SV-07 VALVE: Runze binary ══════════════════════════════════════════════════
def _chk(data):
    t = sum(data)
    return [t & 0xFF, (t >> 8) & 0xFF]

def valve_send(func, b3=0x00, b4=0x00, wait=0.5, label=""):
    ser.reset_input_buffer()
    frame = [0xCC, VALVE_ADDR, func, b3, b4, 0xDD]
    ser.write(bytes(frame + _chk(frame)))
    time.sleep(wait)
    resp = ser.read(8)
    if label:
        print(f"  [valve {label}] <- {resp.hex() if resp else 'NONE'}")
    return resp

def valve_move(port):
    print(f"  Moving switch valve -> port {port}")
    valve_send(0x44, port, 0x00, wait=3, label=f"port{port}")
    for _ in range(20):                       # wait until motor idle
        r = valve_send(0x4A, wait=0.3)
        if len(r) >= 3 and r[2] == 0x00:
            break

def valve_reset():
    print("  Resetting switch valve")
    valve_send(0x45, wait=3, label="reset")

# ══ HIGH-LEVEL STEPS ═══════════════════════════════════════════════════════════
def aspirate(speed=ASPIRATE_SPEED):
    print(f"\n-- ASPIRATE {VOLUME_ML} ml ({STEPS} steps) at S{speed} --")
    for a in ALL_PUMPS:
        pump_move_valve(a, INTAKE_PORT[a])
    wait_all_idle(ALL_PUMPS, timeout=15, what="intake-valve")
    time.sleep(1)
    fire_all(ALL_PUMPS, f'S{speed}P{STEPS}R')
    wait_all_idle(ALL_PUMPS, timeout=120, what="aspirate")

def dispense(speed=DISPENSE_SPEED):
    print(f"\n-- DISPENSE to pump-output port {OUTPUT_PORT} at S{speed} --")
    for a in ALL_PUMPS:
        pump_move_valve(a, OUTPUT_PORT)
    wait_all_idle(ALL_PUMPS, timeout=15, what="output-valve")
    time.sleep(1)
    fire_all(ALL_PUMPS, f'S{speed}D{STEPS}R')
    wait_all_idle(ALL_PUMPS, timeout=120, what="dispense")

# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 55)
print("CONNECTION TEST")
print("=" * 55)
for a in ALL_PUMPS:
    if not pump_status(a):
        print(f"  [!] pump {a} did NOT respond"); ser.close(); raise SystemExit(1)
print("  All pumps responded.")
time.sleep(1)

# Reset the switch valve once at the start so it knows its origin
valve_reset()
time.sleep(1)

# ── Workflow: pump out, switch valve, pump again ──────────────────────────
# Collection sequence: for each switch-valve port, fill then dispense.
COLLECT_PORTS = [6,8]    # SV-07 ports, it will switch to each port each time

for i, vport in enumerate(COLLECT_PORTS, 1):
    print("\n" + "=" * 55)
    print(f"CYCLE {i}/{len(COLLECT_PORTS)}  ->  switch-valve port {vport}")
    print("=" * 55)

    # 1. fill the syringes
    aspirate()
    time.sleep(1)

    # 2. route the shared output line to this switch-valve port
    valve_move(vport)
    time.sleep(1)

    # 3. push the liquid out through that port
    dispense()
    time.sleep(20)

ser.close()
print("\nDone!")