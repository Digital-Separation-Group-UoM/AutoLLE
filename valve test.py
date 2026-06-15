import serial
import time

# ── Configuration ─────────────────────────────────────────────────────────────
# PORT      = '/dev/tty.usbserial-130'
PORT = 'COM6'
BAUD = 9600
PUMP_ADDR = '4'    # DT ASCII address (switch position 2)
VALVE_ADDR = 0x01  # Runze binary address

# ── Serial connection ──────────────────────────────────────────────────────────
ser = serial.Serial(PORT, baudrate=BAUD,
                    bytesize=8, parity='N', stopbits=1,
                    timeout=2, rtscts=False, xonxoff=False)
ser.setRTS(True)
ser.setDTR(True)
time.sleep(0.5)

# ── Runze binary (SV-07M switch valve) ────────────────────────────────────────
def _chk(data):
    t = sum(data)
    return [t & 0xFF, (t >> 8) & 0xFF]

def valve_send(func, b3=0x00, b4=0x00, wait=0.5, label=""):
    ser.reset_input_buffer()
    frame = [0xCC, VALVE_ADDR, func, b3, b4, 0xDD]
    pkt = bytes(frame + _chk(frame))
    ser.write(pkt)
    time.sleep(wait)
    resp = ser.read(8)
    if label:
        print(f"  [valve {label}] ← {resp.hex() if resp else 'NONE'}")
    return resp

def valve_move(port):
    print(f"  Moving switch valve → port {port}")
    valve_send(0x44, port, 0x00, wait=3, label=f"→port{port}")
    # Wait until motor idle
    for _ in range(20):
        r = valve_send(0x4A, wait=0.3)
        if len(r) >= 3 and r[2] == 0x00:
            break

def valve_reset():
    print("  Resetting switch valve")
    valve_send(0x45, wait=3, label="reset")

def valve_status():
    return valve_send(0x4A, wait=0.3, label="status")

# ── DT ASCII (SY-01B syringe pump) ────────────────────────────────────────────
def pump_send(cmd, wait=1, label=""):
    ser.reset_input_buffer()
    frame = f'/{PUMP_ADDR}{cmd}\r'.encode()
    ser.write(frame)
    time.sleep(wait)
    resp = ser.read_all()
    if label:
        print(f"  [pump  {label}] ← {resp}")
    return resp

def pump_status():
    return pump_send('Q', wait=0.5, label="status")

def pump_init():
    print("  Initializing pump (homing syringe + valve)...")
    pump_send('ZR', wait=15, label="init")

def pump_move_valve(port):
    print(f"  Moving pump valve → port {port}")
    pump_send(f'I{port}R', wait=3, label=f"valve→{port}")

def pump_absolute(steps, wait=6):
    print(f"  Plunger absolute → {steps} steps")
    pump_send(f'A{steps}R', wait=wait, label=f"abs{steps}")

def pump_aspirate(steps, wait=6):
    print(f"  Aspirating {steps} steps")
    pump_send(f'P{steps}R', wait=wait, label=f"pickup{steps}")

def pump_dispense(steps, wait=6):
    print(f"  Dispensing {steps} steps")
    pump_send(f'D{steps}R', wait=wait, label=f"dispense{steps}")

def pump_set_speed(speed_code):
    """Speed code 0 (fastest) to 40 (slowest), default=11"""
    pump_send(f'S{speed_code}', wait=0.3, label=f"speed{speed_code}")

# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 50)
print("Switch valve:")
valve_status()

valve_reset()

print("\n" + "=" * 50)
print("3. VALVE MOVEMENT TEST")
print("=" * 50)
for port in [1, 3, 6, 9]:
    valve_move(port)


ser.close()
print("\nDone!")