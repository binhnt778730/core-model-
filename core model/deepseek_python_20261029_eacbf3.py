"""
process_simulator.py

Mô phỏng Process State, PCB, Ready/Waiting Queue và Context Switch.

Chạy demo:  python process_simulator.py
"""

from collections import deque
from enum import Enum


# ============================================================
# 1. PROCESS STATE + STATE TRANSITION
# ============================================================

class ProcessState(Enum):
    NEW        = "NEW"
    READY      = "READY"
    RUNNING    = "RUNNING"
    WAITING    = "WAITING"
    TERMINATED = "TERMINATED"


# Bảng chuyển đổi hợp lệ
VALID_TRANSITIONS = {
    (ProcessState.NEW,        ProcessState.READY):      "admit",
    (ProcessState.READY,      ProcessState.RUNNING):    "dispatch",
    (ProcessState.RUNNING,    ProcessState.READY):      "preempt",
    (ProcessState.RUNNING,    ProcessState.WAITING):    "io_request",
    (ProcessState.RUNNING,    ProcessState.TERMINATED): "exit",
    (ProcessState.WAITING,    ProcessState.READY):      "io_complete",
}


def transition(pcb, new_state):
    """Chuyển trạng thái; raise nếu không hợp lệ."""
    if (pcb.state, new_state) not in VALID_TRANSITIONS:
        raise ValueError(
            f"Chuyển đổi không hợp lệ: {pcb.state.value} → {new_state.value}"
        )
    pcb.state = new_state


# ============================================================
# 2. PCB — PROCESS CONTROL BLOCK
# ============================================================

class PCB:
    """Lưu toàn bộ dữ liệu của một tiến trình."""

    def __init__(self, pid, name, burst_time=10):
        # Định danh
        self.pid  = pid
        self.name = name

        # Trạng thái
        self.state = ProcessState.NEW

        # Ngữ cảnh CPU (được lưu/khôi phục khi context switch)
        self.program_counter = 0
        self.cpu_registers   = [0] * 8
        self.stack_pointer   = 0x7FFF0000
        self.status_flags    = 0

        # Lập lịch
        self.burst_time     = burst_time
        self.remaining_time = burst_time
        self.quantum_used   = 0

        # I/O
        self.io_remaining = 0


# ============================================================
# 3. READY QUEUE / WAITING QUEUE
# ============================================================

class ReadyQueue:
    """Hàng đợi tiến trình ở trạng thái READY."""

    def __init__(self):
        self.q = deque()

    def enqueue(self, pcb):
        pcb.state = ProcessState.READY
        self.q.append(pcb)

    def dequeue(self):
        return self.q.popleft() if self.q else None

    def __len__(self):
        return len(self.q)

    def __iter__(self):
        return iter(self.q)


class WaitingQueue:
    """Hàng đợi tiến trình đang chờ I/O."""

    def __init__(self):
        self.q = []

    def enqueue(self, pcb, io_time):
        pcb.state = ProcessState.WAITING
        pcb.io_remaining = io_time
        self.q.append(pcb)

    def tick(self):
        """Giảm io_remaining; trả về danh sách hoàn tất I/O."""
        done = []
        for pcb in self.q[:]:
            pcb.io_remaining -= 1
            if pcb.io_remaining <= 0:
                self.q.remove(pcb)
                done.append(pcb)
        return done

    def __len__(self):
        return len(self.q)

    def __iter__(self):
        return iter(self.q)


# ============================================================
# 4. CPU (GIẢ LẬP) — giữ ngữ cảnh đang chạy
# ============================================================

class CPU:
    def __init__(self):
        self.current   = None
        self.pc        = 0
        self.registers = [0] * 8
        self.sp        = 0
        self.flags     = 0


# ============================================================
# 5. CONTEXT SWITCH ENGINE — lưu/khôi phục dữ liệu
# ============================================================

class ContextSwitchEngine:
    def __init__(self, cpu, ready_q, waiting_q, log=print):
        self.cpu       = cpu
        self.ready_q   = ready_q
        self.waiting_q = waiting_q
        self.log       = log
        self.count     = 0

    def switch(self, next_pcb, reason="dispatch"):
        prev = self.cpu.current
        if prev is next_pcb:
            return False

        # --- BƯỚC 1: LƯU ngữ cảnh tiến trình cũ vào PCB ---
        if prev is not None:
            prev.program_counter = self.cpu.pc
            prev.cpu_registers   = self.cpu.registers.copy()
            prev.stack_pointer   = self.cpu.sp
            prev.status_flags    = self.cpu.flags
            self.log(
                f"[SWITCH-OUT] PID {prev.pid} ({prev.name}) "
                f"lưu PC={prev.program_counter}, "
                f"regs={prev.cpu_registers}, "
                f"SP={hex(prev.stack_pointer)}, "
                f"flags={prev.status_flags}"
            )

        # --- BƯỚC 2: KHÔI PHỤC ngữ cảnh tiến trình mới từ PCB ---
        if next_pcb is not None:
            self.cpu.current   = next_pcb
            self.cpu.pc        = next_pcb.program_counter
            self.cpu.registers = next_pcb.cpu_registers.copy()
            self.cpu.sp        = next_pcb.stack_pointer
            self.cpu.flags     = next_pcb.status_flags
            self.log(
                f"[SWITCH-IN ] PID {next_pcb.pid} ({next_pcb.name}) "
                f"khôi phục PC={next_pcb.program_counter}, "
                f"regs={next_pcb.cpu_registers}, "
                f"SP={hex(next_pcb.stack_pointer)}, "
                f"flags={next_pcb.status_flags} "
                f"(lý do: {reason})"
            )

        self.count += 1
        return True


# ============================================================
# 6. SCHEDULER — ROUND-ROBIN
# ============================================================

class Scheduler:
    def __init__(self, engine, quantum=4):
        self.engine  = engine
        self.quantum = quantum

    def tick(self):
        cpu = self.engine.cpu
        cur = cpu.current

        if cur is None:
            self.dispatch_next()
            return

        # Thực thi 1 đơn vị thời gian
        cpu.pc += 1
        cur.remaining_time -= 1
        cur.quantum_used   += 1

        if cur.remaining_time <= 0:
            self.terminate(cur)
            return

        if cur.quantum_used >= self.quantum:
            self.preempt(cur)

    def dispatch_next(self):
        nxt = self.engine.ready_q.dequeue()
        if nxt is None:
            return
        transition(nxt, ProcessState.RUNNING)
        nxt.quantum_used = 0
        self.engine.switch(nxt, reason="dispatch")

    def preempt(self, pcb):
        transition(pcb, ProcessState.READY)
        pcb.quantum_used = 0
        self.engine.ready_q.enqueue(pcb)
        self.engine.log(f"[PREEMPT   ] PID {pcb.pid} hết quantum → READY")
        self.dispatch_next()

    def terminate(self, pcb):
        transition(pcb, ProcessState.TERMINATED)
        self.engine.log(f"[EXIT      ] PID {pcb.pid} ({pcb.name}) kết thúc")
        self.engine.cpu.current = None
        self.dispatch_next()


# ============================================================
# 7. KERNEL — ghép mọi thứ
# ============================================================

class Kernel:
    def __init__(self, quantum=4, log=print):
        self.log       = log
        self.cpu       = CPU()
        self.ready_q   = ReadyQueue()
        self.waiting_q = WaitingQueue()
        self.engine    = ContextSwitchEngine(
            self.cpu, self.ready_q, self.waiting_q, log
        )
        self.sched     = Scheduler(self.engine, quantum=quantum)
        self.processes = {}
        self.next_pid  = 1
        self.clock     = 0

    def create_process(self, name, burst, io_time=0):
        pid = self.next_pid
        self.next_pid += 1
        pcb = PCB(pid, name, burst)
        self.processes[pid] = pcb
        self.log(f"[CREATE] PID {pid} ({name}) burst={burst} io={io_time}")
        transition(pcb, ProcessState.READY)
        self.ready_q.enqueue(pcb)
        return pcb

    def request_io(self, pcb, io_time):
        transition(pcb, ProcessState.WAITING)
        self.waiting_q.enqueue(pcb, io_time)
        self.log(f"[I/O REQ] PID {pcb.pid} chờ I/O {io_time} tick")
        self.cpu.current = None
        self.sched.dispatch_next()

    def step(self):
        self.clock += 1
        self.log(f"\n===== TICK {self.clock} =====")

        # I/O hoàn tất → READY
        for pcb in self.waiting_q.tick():
            transition(pcb, ProcessState.READY)
            self.ready_q.enqueue(pcb)
            self.log(f"[I/O DONE ] PID {pcb.pid} → READY")

        # Scheduler chạy
        self.sched.tick()

        # In trạng thái
        cur = self.cpu.current
        self.log(
            f"CPU={cur.name if cur else 'idle'} | "
            f"Ready={[p.pid for p in self.ready_q]} | "
            f"Waiting={[p.pid for p in self.waiting_q]}"
        )

    def run(self, ticks):
        for _ in range(ticks):
            self.step()


# ============================================================
# 8. DEMO
# ============================================================

if __name__ == "__main__":
    k = Kernel(quantum=4)
    k.create_process("A", burst=6)
    k.create_process("B", burst=4)
    k.create_process("C", burst=5)

    # Giữa chừng cho A xin I/O để thấy WAITING → READY
    k.run(ticks=3)
    a = k.processes[1]
    k.request_io(a, io_time=3)
    k.run(ticks=20)

    print("\n===== TỔNG KẾT =====")
    for pid, p in k.processes.items():
        print(f"PID {pid} ({p.name}): "
              f"state={p.state.value}, remaining={p.remaining_time}")
    print(f"Tổng context switch: {k.engine.count}")