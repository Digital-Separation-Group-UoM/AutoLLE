import serial
import time

# ── Configuration ─────────────────────────────────────────────────────────────
PORT = 'COM6'
BAUD = 9600

# Pump groups
AQUEOUS = ['1', '2', '3']     # aqueous phase
ORGANIC = ['4']
ALL_PUMPS = AQUEOUS + ORGANIC
# ALL_PUMPS = ['4']# for single use

# Per-pump INPUT valve port for aspiration
INTAKE_PORT = {'1': 1, '2': 1, '3': 1, '4': 12}
OUTPUT_PORT = 8               # shared output port for dispensing (all pumps)

# Pump moving speed, check the table for speed setting
ASPIRATE_SPEED = 4
DISPENSE_SPEED = 16

# Syringe calibration
SYRINGE_FULL_STEPS = 12000    # full stroke
SYRINGE_VOLUME_ML  = 5.0      # syringe volume
def ml_to_steps(ml):
    return int(round(ml / SYRINGE_VOLUME_ML * SYRINGE_FULL_STEPS))

VOLUME_ML = 2.0
STEPS = ml_to_steps(VOLUME_ML)   # 4 ml -> 9600 steps

# ── Serial connection ──────────────────────────────────────────────────────────
ser = serial.Serial(PORT, baudrate=BAUD,
                    bytesize=8, parity='N', stopbits=1,
                    timeout=2, rtscts=False, xonxoff=False)
ser.setRTS(True)
ser.setDTR(True)
time.sleep(0.5)

# ── DT ASCII (SY-01B syringe pump) ────────────────────────────────────────────
def pump_send(addr, cmd, wait=0.15, read=True, label=""):
    ser.reset_input_buffer()
    frame = f'/{addr}{cmd}\r'.encode()
    ser.write(frame)
    time.sleep(wait)
    resp = ser.read_all() if read else b''
    if label:
        print(f"  [pump {addr} {label}] <- {resp}")
    return resp

def pump_status(addr):
    return pump_send(addr, 'Q', wait=0.1, label="status")

def pump_is_busy(addr):
    """Idle bit 0x20 set -> idle. Missing reply treated as busy.
    Verify the raw bytes in section 1 and adjust the mask if your pump differs."""
    r = pump_status(addr)
    if not r or len(r) < 3:
        return True
    return not (r[2] & 0x20)

def pump_init(addr):
    print(f"  Init pump {addr}")
    pump_send(addr, 'ZR', wait=0.15, read=False)

def pump_set_speed(addr, speed_code):
    pump_send(addr, f'S{speed_code}', wait=0.1, label=f"speed{speed_code}")

def pump_move_valve(addr, port):
    """Switch the pump's internal distribution valve to `port`."""
    pump_send(addr, f'I{port}R', wait=0.15, read=False)
    print(f"  -> pump {addr}: valve to port {port}")

# ── Parallel helpers ───────────────────────────────────────────────────────────
def fire_all(addrs, cmd):
    for a in addrs:
        pump_send(a, cmd, wait=0.05, read=False)
        print(f"  -> pump {a}: {cmd}")

def wait_all_idle(addrs, timeout=30, poll=0.4, what=""):
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

# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 55)
print("1. CONNECTION TEST -- every pump must answer")
print("=" * 55)
ok = True
for a in ALL_PUMPS:
    r = pump_status(a)
    if not r:
        print(f"  [!] pump {a} did NOT respond -- check address/wiring")
        ok = False
if not ok:
    print("\nOne or more pumps unreachable. Aborting.")
    ser.close()
    raise SystemExit(1)
print("  All pumps responded.")

time.sleep(1)

print("\n" + "=" * 55)
print("2. INITIALISE ALL PUMPS IN PARALLEL")
print("=" * 55)
for a in ALL_PUMPS:
    pump_init(a)
wait_all_idle(ALL_PUMPS, timeout=30, what="init")
for a in ALL_PUMPS:
    pump_set_speed(a, 40)

time.sleep(2)


print("\n" + "=" * 55)
print(f"3. ASPIRATE {VOLUME_ML} ml ({STEPS} steps) -- ALL PUMPS IN PARALLEL")
print("=" * 55)
print("  Switching intake valves:")
print("    aqueous: pump1->port1, pump2->port2, pump3->port3")
print("    organic: pump4->port1")
for a in ALL_PUMPS:
    pump_move_valve(a, INTAKE_PORT[a])
wait_all_idle(ALL_PUMPS, timeout=15, what="valve-switch")

time.sleep(2)

print("  Aspirating (parallel):")
t0 = time.time()
# fire_all(ALL_PUMPS, f'P{STEPS}R')        # P = relative aspirate
fire_all(ALL_PUMPS, f'S{ASPIRATE_SPEED}P{STEPS}R')
wait_all_idle(ALL_PUMPS, timeout=30, what="aspirate")
print(f"  Aspirate elapsed: {time.time()-t0:.1f}s")

time.sleep(2)

print("\n" + "=" * 55)
print(f"4. DISPENSE -- switch all valves to port {OUTPUT_PORT}, push out IN PARALLEL")
print("=" * 55)
for a in ALL_PUMPS:
    pump_move_valve(a, OUTPUT_PORT)
wait_all_idle(ALL_PUMPS, timeout=15, what="valve-switch")


time.sleep(2)

print("  Dispensing (parallel):")
t0 = time.time()
fire_all(ALL_PUMPS, f'S{DISPENSE_SPEED}D{STEPS}R')
wait_all_idle(ALL_PUMPS, timeout=120, what="dispense")
print(f"  Dispense elapsed: {time.time()-t0:.1f}s")

ser.close()
print("\nDone!")