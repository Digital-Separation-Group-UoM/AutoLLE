"""
stage test.py
====================
Python controller for the motorized cuvette stage (电动比色皿支架).
Communicates via Modbus RTU over RS-485 / USB.

Protocol summary (from 电动比色皿支架简要协议.docx):
  - Default address : 0x01
  - Default serial  : 115200 8N1
  - 3200 pulses     = 1 motor revolution = 4 mm linear travel
  - Channel 0 (home/zero) → first channel offset: 0x3E00 (15872) pulses
  - Between subsequent channels: 0x3200 (12800) pulses

Registers used:
  Read  (FC 04):
    0x0033 (×2 regs) → cumulative pulse count (uint32)
    0x0034 (×1 reg ) → IO / limit-switch status
    0x00F1 (×1 reg ) → motor status (0–6)

  Write single (FC 06):
    0x0092 = 0x0001  → set zero point (clear pulse counter)
    0x00F7 = 0x0001  → emergency stop

  Write multiple (FC 10h):
    0x00FD (4 regs, 8 bytes) → relative move  [dir, acc, speed(×2), pulses(×4)]
    0x00FE (4 regs, 8 bytes) → absolute move  [acc(×2), speed(×2), absPulses(×4)]

Requirements:
    pip install pymodbus pyserial

Usage examples:
    python stage test.py                     # interactive menu
    python stage test.py --channel 3         # move to channel 3, then exit
    python stage test.py --reset             # home/reset only
    python stage test.py --status            # read position & motor state
    python stage test.py --port COM3         # override port
"""

import argparse
import json
import struct
import time

try:
    from pymodbus.client import ModbusSerialClient
    from pymodbus.exceptions import ModbusException
except ImportError:
    raise SystemExit("pymodbus not found. Run:  pip install pymodbus pyserial")


# ── Register addresses ────────────────────────────────────────────────────────
REG_PULSE_COUNT  = 0x0033   # FC04, 2 registers → uint32 cumulative pulses
REG_IO_STATUS    = 0x0034   # FC04, 1 register  → limit-switch bits
REG_MOTOR_STATUS = 0x00F1   # FC04, 1 register  → motor state (0–6)
REG_SET_ZERO     = 0x0092   # FC06, value 0x0001 → clear pulse counter
REG_ESTOP        = 0x00F7   # FC06, value 0x0001 → emergency stop
REG_MOVE_REL     = 0x00FD   # FC10, 4 registers  → relative move
REG_MOVE_ABS     = 0x00FE   # FC10, 4 registers  → absolute move

# Motor status codes
MOTOR_STATUS = {
    0: "query failed",
    1: "stopped",
    2: "accelerating",
    3: "decelerating",
    4: "full speed",
    5: "homing",
    6: "calibrating",
}

DEFAULT_ACC   = 50    # acceleration 0–255
DEFAULT_SPEED = 200   # speed in RPM (0–3000)


def load_config(path=None) -> dict:
    """Load conf.json; fall back to built-in defaults if not found."""
    defaults = {
        "dev_info": {"port": "COM5", "addr": 1, "baud": 115200, "count": 8},
        "电机切换点": {
            "0": 0, "1": 15872, "2": 12800, "3": 12800, "4": 12800,
            "5": 12800, "6": 12800, "7": 12800, "8": 12800,
        },
    }
    candidate = path or "conf.json"
    try:
        with open(candidate, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[config] loaded from {candidate}")
        return data
    except FileNotFoundError:
        print(f"[config] {candidate} not found – using built-in defaults")
        return defaults


class StageController:
    """Controls the motorized cuvette stage over Modbus RTU."""

    def __init__(self, config_path=None, port=None):
        cfg = load_config(config_path)
        dev = cfg["dev_info"]
        self.port         = port or dev.get("port", "COM5")
        self.addr         = int(dev.get("addr", 1))
        self.baud         = int(dev.get("baud", 115200))
        self.num_channels = int(dev.get("count", 8))
        self.offsets      = {int(k): int(v) for k, v in cfg["电机切换点"].items()}
        self._client      = None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        self._client = ModbusSerialClient(
            port=self.port,
            baudrate=self.baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=1,
        )
        ok = self._client.connect()
        if ok:
            print(f"[OK] connected → {self.port} @ {self.baud} baud, "
                  f"slave addr=0x{self.addr:02X}")
        else:
            print(f"[ERROR] could not open {self.port}")
        return ok

    def disconnect(self):
        if self._client:
            self._client.close()
            print("[OK] disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _fc04(self, register: int, count: int):
        """Read input registers (FC 04). count must be passed as keyword in pymodbus 3.x."""
        r = self._client.read_input_registers(register, count=count, slave=self.addr)
        if r.isError():
            raise ModbusException(f"FC04 read {register:#06x} failed: {r}")
        return r.registers

    def _fc06(self, register: int, value: int):
        """Write single holding register (FC 06)."""
        r = self._client.write_register(register, value, slave=self.addr)
        if r.isError():
            raise ModbusException(f"FC06 write {register:#06x} failed: {r}")

    def _fc10(self, register: int, raw_bytes: bytes):
        """Write multiple holding registers (FC 10 / 0x10)."""
        assert len(raw_bytes) % 2 == 0, "raw_bytes must be an even length"
        values = [int.from_bytes(raw_bytes[i:i+2], "big")
                  for i in range(0, len(raw_bytes), 2)]
        r = self._client.write_registers(register, values, slave=self.addr)
        if r.isError():
            raise ModbusException(f"FC10 write {register:#06x} failed: {r}")

    # ── Read functions ────────────────────────────────────────────────────────

    def read_pulse_count(self) -> int:
        """
        Return current cumulative pulse count as a signed integer.
        The device counts positively towards home and negatively towards the far end.
        """
        regs = self._fc04(REG_PULSE_COUNT, 2)
        raw  = struct.pack(">HH", regs[0], regs[1])
        u32  = struct.unpack(">I", raw)[0]
        # Convert to signed: negative direction wraps as (2^32 - pulses)
        return u32 if u32 <= 0x7FFFFFFF else u32 - (1 << 32)

    def read_motor_status(self) -> int:
        """Return motor status code (0–6; see MOTOR_STATUS dict)."""
        return self._fc04(REG_MOTOR_STATUS, 1)[0] & 0xFF

    def read_io_status(self) -> dict:
        """Return limit-switch states. bit0=left(home), bit1=right(far)."""
        val = self._fc04(REG_IO_STATUS, 1)[0] & 0xFF
        return {
            "left_limit":  bool(val & 0x01),  # home/zero side
            "right_limit": bool(val & 0x02),  # far side
        }

    def is_moving(self) -> bool:
        return self.read_motor_status() not in (0, 1)

    def wait_until_stopped(self, timeout: float = 30.0) -> bool:
        """Block until motor reports stopped, or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.read_motor_status()
            print(f"\r  motor: {status} – {MOTOR_STATUS.get(status, '?')}   ",
                  end="", flush=True)
            if status == 1:
                print()
                return True
            time.sleep(0.2)
        print("\n[WARN] timed out waiting for motor to stop")
        return False

    # ── Motor commands ────────────────────────────────────────────────────────

    def set_zero(self):
        """Define the current position as zero (clear pulse counter)."""
        self._fc06(REG_SET_ZERO, 0x0001)
        print("[OK] zero point set")

    def emergency_stop(self):
        """Send emergency stop. Do NOT use at speeds > 1000 RPM."""
        self._fc06(REG_ESTOP, 0x0001)
        print("[OK] emergency stop sent")

    def move_relative(self, pulses: int, direction: int = 0,
                      acc: int = DEFAULT_ACC, speed: int = DEFAULT_SPEED,
                      wait: bool = True):
        """
        Move by a relative number of pulses.

        Args:
            pulses    : pulse count to travel (0 – 0xFFFFFFFF)
            direction : 0 = away from home (far),  1 = towards home (zero)
            acc       : acceleration 0–255
            speed     : speed in RPM (0–3000)
            wait      : if True, block until motor stops
        """
        payload = struct.pack(">B B H I",
                              direction & 0x01,
                              acc & 0xFF,
                              speed & 0xFFFF,
                              pulses & 0xFFFFFFFF)
        self._fc10(REG_MOVE_REL, payload)
        print(f"[OK] relative move → {pulses} pulses "
              f"{'→home' if direction else '→far'}, speed={speed} RPM")
        if wait:
            self.wait_until_stopped()

    def move_absolute(self, abs_pulses: int,
                      acc: int = DEFAULT_ACC, speed: int = DEFAULT_SPEED,
                      wait: bool = True):
        """
        Move to an absolute pulse position (relative to the zero point).

        Args:
            abs_pulses : target position as signed int32
                         negative → towards far end
                         positive → towards home
            acc        : acceleration 0–255
            speed      : speed in RPM (0–3000)
            wait       : if True, block until motor stops
        """
        payload = struct.pack(">H H i",
                              acc & 0xFFFF,
                              speed & 0xFFFF,
                              abs_pulses)
        self._fc10(REG_MOVE_ABS, payload)
        print(f"[OK] absolute move → pulse={abs_pulses}, speed={speed} RPM")
        if wait:
            self.wait_until_stopped()

    def reset(self, speed: int = DEFAULT_SPEED):
        """
        Home the stage by driving towards the left/zero limit switch.
        Once the limit triggers (motor stops), the zero point is set.
        """
        print("[..] homing – driving towards left limit switch...")
        # Drive a very large number of pulses homeward; the limit switch stops it
        self.move_relative(pulses=0xFFFFFF, direction=1,
                           acc=DEFAULT_ACC, speed=speed, wait=True)
        time.sleep(0.1)
        io = self.read_io_status()
        if io["left_limit"]:
            self.set_zero()
            print("[OK] homing complete – left limit reached, zero point set")
        else:
            print("[WARN] motor stopped but left limit was NOT triggered; "
                  "check wiring/mechanical limits")

    # ── Channel movement ──────────────────────────────────────────────────────

    def _channel_abs_position(self, channel: int) -> int:
        """
        Compute the absolute pulse position for a channel.

        From the protocol:
          channel 0 → 0 (home)
          channel 1 → -0x3E00 (15872 pulses away from home)
          channel N → channel 1 + cumulative offsets for channels 2..N
                      (each subsequent channel adds 0x3200 = 12800 pulses)

        The stage travels in the negative direction away from home,
        so the target is always ≤ 0.
        """
        total = 0
        for i in range(1, channel + 1):
            total += self.offsets.get(i, 12800)
        return -total  # negative = away from home

    def move_to_channel(self, channel: int,
                        speed: int = DEFAULT_SPEED, wait: bool = True):
        """Move to a numbered channel slot. Channel 0 is the home position."""
        if channel < 0 or channel > self.num_channels:
            raise ValueError(
                f"Channel must be 0–{self.num_channels}, got {channel}")
        target = self._channel_abs_position(channel)
        print(f"[..] moving to channel {channel}  "
              f"(target pulse = {target})")
        self.move_absolute(target, acc=DEFAULT_ACC, speed=speed, wait=wait)

    # ── Status display ────────────────────────────────────────────────────────

    def print_status(self):
        """Print a summary of the current stage state."""
        pulses = self.read_pulse_count()
        motor  = self.read_motor_status()
        io     = self.read_io_status()
        mm     = abs(pulses) / 3200 * 4   # 3200 pulses/rev, 4 mm/rev
        print(f"  Pulse position : {pulses:>10}  ({mm:.3f} mm from zero)")
        print(f"  Motor status   : {motor} – {MOTOR_STATUS.get(motor, '?')}")
        print(f"  Left  limit    : {'TRIGGERED ◄' if io['left_limit']  else 'clear'}")
        print(f"  Right limit    : {'TRIGGERED ►' if io['right_limit'] else 'clear'}")


# ── Interactive menu ──────────────────────────────────────────────────────────

def interactive(stage: StageController):
    print("\nMotorized Stage – Interactive Menu")
    print("=" * 38)
    while True:
        print(f"\n  s        show status")
        print(f"  r        reset / home")
        print(f"  0–{stage.num_channels}      move to channel N")
        print(f"  a        absolute move (enter pulse count)")
        print(f"  e        emergency stop")
        print(f"  q        quit")
        cmd = input(">>> ").strip().lower()

        if cmd == "q":
            break
        elif cmd == "s":
            stage.print_status()
        elif cmd == "r":
            stage.reset()
        elif cmd == "e":
            stage.emergency_stop()
        elif cmd == "a":
            try:
                p = int(input("  Absolute pulse target (negative = far end): "))
                stage.move_absolute(p)
            except ValueError:
                print("  Invalid number.")
        elif cmd.isdigit() and 0 <= int(cmd) <= stage.num_channels:
            try:
                stage.move_to_channel(int(cmd))
            except ValueError as exc:
                print(f"  {exc}")
        else:
            print("  Unknown command.")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Motorized cuvette stage controller")
    parser.add_argument("--port",    help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("--config",  help="Path to conf.json")
    parser.add_argument("--channel", type=int,
                        help="Move to this channel number, then exit")
    parser.add_argument("--reset",   action="store_true",
                        help="Home the stage, then exit")
    parser.add_argument("--status",  action="store_true",
                        help="Print current status, then exit")
    args = parser.parse_args()

    with StageController(config_path=args.config, port=args.port) as stage:
        if args.status:
            stage.print_status()
        elif args.reset:
            stage.reset()
        elif args.channel is not None:
            stage.move_to_channel(args.channel)
        else:
            interactive(stage)


if __name__ == "__main__":
    # main()
    with StageController() as stage:
        stage.reset()
        time.sleep(2)
        stage.move_to_channel(6)
