#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SLG46826G HEX Writer for Arduino UNO Bridge
------------------------------------------------------------
GreenPAK DesignerからExportしたIntel HEXを選択し、
Arduino UNOに書き込んだブリッジファーム経由で
SLG46826GのNVM page 0〜14へ書き込みます。

必要:
    pip install pyserial

実行:
    python greenpak_hex_writer_v06.py
"""

import os
import sys
import time
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


APP_TITLE = "SLG46826G HEX Writer via Arduino UNO v0.6"
BAUDRATE = 115200
PAGE_SIZE = 16
NVM_SIZE = 256
PROGRAM_PAGES = 15  # page 0〜14。page15はSLG46826系のサービスページ扱いとして書かない。


class IntelHexError(Exception):
    pass


def _clean_intel_hex_line(raw_line: str) -> str:
    """
    GreenPAK DesignerのHEXファイルに、BOM、NUL、不可視文字、コピー時の余計な文字が
    混ざった場合でも、Intel HEXの ':' から始まる本体だけを取り出す。
    """
    line = raw_line.replace("\x00", "").strip()
    line = line.lstrip("\ufeff\ufffe")
    colon = line.find(":")
    if colon > 0:
        prefix = line[:colon]
        # UTF-8 BOM化け、空白、不可視文字程度なら捨てる。
        if all((not ch.isprintable()) or ch.isspace() or ch in "\ufeff\ufffeï»¿" for ch in prefix):
            line = line[colon:]
    return line.strip()


def parse_intel_hex(path: str) -> bytes:
    """
    Intel HEXを読み、先頭256バイトのNVMイメージとして返す。
    未定義アドレスは0x00で埋める。

    v0.6:
      - UTF-8 BOM付きHEXに対応
      - 行頭に混ざった不可視文字/NULを除去
      - GreenPAK Designerの通常Export NVM形式、16行×16バイト＋EOFに対応
    """
    mem = {}
    upper = 0
    eof_seen = False

    # バイナリで読み、BOMや文字化けに強くする。
    raw_bytes = open(path, "rb").read()
    text = raw_bytes.decode("utf-8-sig", errors="ignore")

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _clean_intel_hex_line(raw)
        if not line:
            continue

        if not line.startswith(":"):
            raise IntelHexError(f"{lineno}行目: Intel HEX行ではありません: {line[:60]}")

        if len(line) < 11:
            raise IntelHexError(f"{lineno}行目: 行が短すぎます")

        try:
            count = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            rectype = int(line[7:9], 16)
            data_hex = line[9:9 + count * 2]
            checksum_text = line[9 + count * 2:11 + count * 2]
            checksum = int(checksum_text, 16)
        except Exception as e:
            raise IntelHexError(f"{lineno}行目: HEX形式を解析できません: {line[:60]}") from e

        if len(data_hex) != count * 2:
            raise IntelHexError(f"{lineno}行目: データ長が不正です")
        if len(checksum_text) != 2:
            raise IntelHexError(f"{lineno}行目: チェックサム長が不正です")

        bytes_for_sum = [count, (addr >> 8) & 0xFF, addr & 0xFF, rectype]
        try:
            data = [int(data_hex[i:i+2], 16) for i in range(0, len(data_hex), 2)]
        except ValueError as e:
            raise IntelHexError(f"{lineno}行目: データ部に16進数以外の文字があります") from e

        total = (sum(bytes_for_sum) + sum(data) + checksum) & 0xFF
        if total != 0:
            raise IntelHexError(f"{lineno}行目: チェックサム不一致: {line[:60]}")

        if rectype == 0x00:
            base = upper + addr
            for i, b in enumerate(data):
                mem[base + i] = b
        elif rectype == 0x01:
            eof_seen = True
            break
        elif rectype == 0x02:
            if count != 2:
                raise IntelHexError(f"{lineno}行目: Extended Segment Address長が不正です")
            upper = ((data[0] << 8) | data[1]) << 4
        elif rectype == 0x04:
            if count != 2:
                raise IntelHexError(f"{lineno}行目: Extended Linear Address長が不正です")
            upper = ((data[0] << 8) | data[1]) << 16
        else:
            # Start Segment/Linear Address等は無視
            pass

    if not mem:
        raise IntelHexError("HEX内にデータレコードがありません")
    if not eof_seen:
        # EOFなしでもデータが完全なら実害は少ないが、ファイル選択ミス検出のため警告的にエラーにする。
        raise IntelHexError("EOFレコード(:00000001FF)がありません")

    min_addr = min(mem.keys())
    max_addr = max(mem.keys())

    # GreenPAK DesignerのExport NVMは通常0x0000から256バイト。
    # 別アドレス基点なら最小アドレスから256バイト切り出す。
    base = 0 if 0 in mem else min_addr

    image = bytearray([0x00] * NVM_SIZE)
    for i in range(NVM_SIZE):
        image[i] = mem.get(base + i, 0x00)

    # データが256バイト未満でも未定義領域0x00埋めにするが、明らかに範囲外だけのHEXは拒否。
    if max_addr < base or min_addr > base + NVM_SIZE - 1:
        raise IntelHexError(f"NVM 0x{base:04X}〜0x{base+NVM_SIZE-1:04X}範囲にデータがありません")

    return bytes(image)


def parse_hex_byte_dump(path: str) -> bytes:
    """
    Intel HEXではなく、AA BB CC... のような256バイト16進ダンプに対応する補助パーサ。
    v0.6では、コロン付きIntel HEXを誤ってここで処理しないようにする。
    """
    raw_bytes = open(path, "rb").read()
    text = raw_bytes.decode("utf-8-sig", errors="ignore")
    if ":" in text:
        raise IntelHexError("Intel HEXらしい ':' を含むため、16進バイトダンプとしては扱いません")
    text = text.replace(",", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    tokens = [t for t in text.split(" ") if t]
    if len(tokens) != NVM_SIZE:
        raise IntelHexError(f"16進バイトダンプとしても256バイトではありません: {len(tokens)}個")
    try:
        data = bytes(int(t, 16) for t in tokens)
    except ValueError as e:
        raise IntelHexError("16進バイトダンプの形式が不正です") from e
    return data


def load_nvm_image(path: str) -> bytes:
    intel_err = None
    try:
        return parse_intel_hex(path)
    except IntelHexError as e:
        intel_err = e

    try:
        return parse_hex_byte_dump(path)
    except IntelHexError as dump_err:
        raise IntelHexError(
            "NVMファイルを解析できません。\n"
            f"Intel HEXとしてのエラー: {intel_err}\n"
            f"16進バイトダンプとしてのエラー: {dump_err}\n"
            "GreenPAK Designerでは File → Export → Export NVM で出力した .hex を選択してください。"
        ) from dump_err

class UnoBridge:
    def __init__(self, port: str, baudrate: int = BAUDRATE, timeout: float = 2.0):
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout, write_timeout=timeout)
        time.sleep(2.0)  # UNOの自動リセット待ち
        self.flush_input()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def flush_input(self):
        self.ser.reset_input_buffer()

    def cmd(self, line: str, timeout: float = 3.0) -> str:
        self.ser.timeout = timeout
        # v0.6: コマンド直前に空行を送らない。
        # 一部環境で空行/接続直後のノイズがUNO側コマンド先頭へ混ざるため。
        self.ser.reset_input_buffer()
        self.ser.write((line.strip() + "\n").encode("ascii"))
        self.ser.flush()

        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            raw = self.ser.readline()
            if not raw:
                continue
            resp = raw.decode("ascii", errors="replace").strip()
            if not resp:
                continue
            last = resp
            # 起動バナーが混ざった場合は読み飛ばす。
            if resp.startswith("OK GPUNO_SLG46826_BRIDGE_READY"):
                continue
            return resp

        raise RuntimeError(
            f"応答なし: {line}\n"
            "UNOへ UNO_BRIDGE_SLG46826G_v06.ino が書き込まれているか、COMポートが正しいか確認してください。"
            + (f"\n最後に受信した行: {last}" if last else "")
        )

    def hello(self) -> str:
        resp = self.cmd("HELLO")
        if resp.startswith("ERR UNKNOWN_CMD"):
            raise RuntimeError(
                "UNO側ファームがPCライター用ブリッジではありません。\n"
                "Arduino IDEで UNO_BRIDGE_SLG46826G_v06/UNO_BRIDGE_SLG46826G_v06.ino をUNOへ書き込んでから、もう一度実行してください。\n"
                "または、別のCOMポートを選んでいる可能性があります。\n"
                f"受信: {resp}"
            )
        if "GPUNO_SLG46826_BRIDGE" not in resp:
            raise RuntimeError(
                "UNOから想定外の応答を受信しました。\n"
                "UNO_BRIDGE_SLG46826G_v06.ino が書き込まれているか確認してください。\n"
                f"受信: {resp}"
            )
        return resp

    def rawscan(self) -> str:
        return self.cmd("RAWSCAN")

    def scan(self) -> str:
        return self.cmd("SCAN")

    def ping(self, control_code: int) -> str:
        return self.cmd(f"PING {control_code}")

    def read_page(self, control_code: int, page: int) -> bytes:
        resp = self.cmd(f"READPAGE {control_code} {page}", timeout=3.0)
        if not resp.startswith("DATA "):
            raise RuntimeError(resp)
        hexs = resp[5:].strip()
        if len(hexs) != PAGE_SIZE * 2:
            raise RuntimeError(f"READPAGE応答長が不正: {resp}")
        return bytes.fromhex(hexs)

    def program_page(self, control_code: int, page: int, data16: bytes) -> str:
        if len(data16) != PAGE_SIZE:
            raise ValueError("data16 must be 16 bytes")
        return self.cmd(f"PROGPAGE {control_code} {page} {data16.hex().upper()}", timeout=5.0)

    def reset(self, control_code: int) -> str:
        return self.cmd(f"RESET {control_code}", timeout=3.0)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x560")

        self.file_path = tk.StringVar()
        self.port_var = tk.StringVar()
        self.cc_var = tk.IntVar(value=1)
        self.reset_after_var = tk.BooleanVar(value=True)
        self.backup_before_var = tk.BooleanVar(value=True)

        self.worker_queue = queue.Queue()
        self.worker = None

        self.create_widgets()
        self.refresh_ports()
        self.after(100, self.process_queue)

    def create_widgets(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="COM Port:").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=28, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky="w", padx=5)
        ttk.Button(top, text="更新", command=self.refresh_ports).grid(row=0, column=2, padx=5)
        ttk.Button(top, text="接続確認", command=self.test_connection).grid(row=0, column=3, padx=5)
        ttk.Button(top, text="Raw I2C Scan", command=self.raw_i2c_scan).grid(row=0, column=4, padx=5)

        ttk.Label(top, text="Control Code:").grid(row=0, column=5, sticky="e", padx=(20, 3))
        self.cc_spin = ttk.Spinbox(top, from_=0, to=15, textvariable=self.cc_var, width=5)
        self.cc_spin.grid(row=0, column=6, sticky="w")

        file_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        file_frame.pack(fill="x")
        ttk.Label(file_frame, text="HEX/NVM:").grid(row=0, column=0, sticky="w")
        ttk.Entry(file_frame, textvariable=self.file_path).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(file_frame, text="選択", command=self.choose_file).grid(row=0, column=2)
        file_frame.columnconfigure(1, weight=1)

        opts = ttk.Frame(self, padding=(10, 0, 10, 10))
        opts.pack(fill="x")
        ttk.Checkbutton(opts, text="書き込み前にNVMをバックアップ", variable=self.backup_before_var).pack(side="left")
        ttk.Checkbutton(opts, text="書き込み後にSoft Resetを送る", variable=self.reset_after_var).pack(side="left", padx=20)

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="NVMバックアップ保存", command=self.backup_only).pack(side="left")
        ttk.Button(buttons, text="HEXを書き込む", command=self.program_hex).pack(side="left", padx=10)

        self.progress = ttk.Progressbar(self, orient="horizontal", maximum=PROGRAM_PAGES, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 8))

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, wrap="none", height=20)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

    def refresh_ports(self):
        if serial is None:
            messagebox.showerror("pyserial未導入", "pip install pyserial を実行してください。")
            return
        ports = list(list_ports.comports())
        values = []
        for p in ports:
            values.append(f"{p.device} - {p.description}")
        self.port_combo["values"] = values
        if values and not self.port_var.get():
            self.port_var.set(values[0])

    def selected_port(self) -> str:
        value = self.port_var.get()
        if not value:
            raise RuntimeError("COMポートを選択してください")
        return value.split(" - ")[0]

    def choose_file(self):
        path = filedialog.askopenfilename(
            title="GreenPAK Designer Export NVM / Intel HEXを選択",
            filetypes=[
                ("HEX/NVM files", "*.hex *.ihx *.nvm *.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.file_path.set(path)

    def log(self, msg: str):
        self.worker_queue.put(("log", msg))

    def set_progress(self, value: int):
        self.worker_queue.put(("progress", value))

    def run_worker(self, target):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("処理中", "現在の処理が終わるまで待ってください。")
            return
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def process_queue(self):
        try:
            while True:
                kind, value = self.worker_queue.get_nowait()
                if kind == "log":
                    self.log_text.insert("end", value + "\n")
                    self.log_text.see("end")
                elif kind == "progress":
                    self.progress["value"] = value
                elif kind == "done":
                    messagebox.showinfo("完了", value)
                elif kind == "error":
                    messagebox.showerror("エラー", value)
        except queue.Empty:
            pass
        self.after(100, self.process_queue)

    def raw_i2c_scan(self):
        def work():
            try:
                port = self.selected_port()
                self.log(f"Raw I2C scan: {port}")
                br = UnoBridge(port)
                try:
                    self.log("UNO: " + br.hello())
                    self.log("RAWSCAN: " + br.rawscan())
                finally:
                    br.close()
            except Exception as e:
                self.worker_queue.put(("error", str(e)))
        self.run_worker(work)

    def test_connection(self):
        def work():
            try:
                port = self.selected_port()
                cc = int(self.cc_var.get())
                self.log(f"接続確認: {port}, control code={cc}")
                br = UnoBridge(port)
                try:
                    self.log("UNO: " + br.hello())
                    self.log("RAWSCAN: " + br.rawscan())
                    self.log("SCAN: " + br.scan())
                    self.log("PING: " + br.ping(cc))
                finally:
                    br.close()
            except Exception as e:
                self.worker_queue.put(("error", str(e)))
        self.run_worker(work)

    def backup_nvm(self, br: UnoBridge, cc: int) -> bytes:
        data = bytearray()
        for page in range(16):
            data.extend(br.read_page(cc, page))
        return bytes(data)

    def backup_only(self):
        save_path = filedialog.asksaveasfilename(
            title="NVMバックアップ保存",
            defaultextension=".bin",
            filetypes=[("Binary", "*.bin"), ("HEX text", "*.txt"), ("All files", "*.*")]
        )
        if not save_path:
            return

        def work():
            try:
                port = self.selected_port()
                cc = int(self.cc_var.get())
                self.log(f"NVMバックアップ読み出し: {port}, control code={cc}")
                br = UnoBridge(port)
                try:
                    self.log("UNO: " + br.hello())
                    self.log("PING: " + br.ping(cc))
                    data = self.backup_nvm(br, cc)
                finally:
                    br.close()

                if save_path.lower().endswith(".txt"):
                    with open(save_path, "w", encoding="ascii") as f:
                        for i in range(0, len(data), 16):
                            f.write(" ".join(f"{b:02X}" for b in data[i:i+16]) + "\n")
                else:
                    with open(save_path, "wb") as f:
                        f.write(data)
                self.log(f"保存しました: {save_path}")
                self.worker_queue.put(("done", "NVMバックアップを保存しました。"))
            except Exception as e:
                self.worker_queue.put(("error", str(e)))
        self.run_worker(work)

    def program_hex(self):
        def work():
            try:
                path = self.file_path.get().strip()
                if not path:
                    raise RuntimeError("HEX/NVMファイルを選択してください")
                if not os.path.exists(path):
                    raise RuntimeError("指定ファイルが存在しません")

                image = load_nvm_image(path)
                if len(image) != NVM_SIZE:
                    raise RuntimeError("NVMイメージが256バイトではありません")

                port = self.selected_port()
                cc = int(self.cc_var.get())

                self.log("=" * 60)
                self.log(f"HEX読込: {path}")
                self.log(f"NVM image: {len(image)} bytes")
                self.log(f"書き込み先: {port}, control code={cc}")
                self.log("注意: page15はSLG46826系のサービスページとして書き込み対象外")

                br = UnoBridge(port)
                try:
                    self.log("UNO: " + br.hello())
                    self.log("PING: " + br.ping(cc))

                    if self.backup_before_var.get():
                        backup = self.backup_nvm(br, cc)
                        ts = time.strftime("%Y%m%d_%H%M%S")
                        backup_path = os.path.join(os.path.dirname(path), f"slg46826g_backup_{ts}.bin")
                        with open(backup_path, "wb") as f:
                            f.write(backup)
                        self.log(f"書き込み前バックアップ保存: {backup_path}")

                    self.set_progress(0)
                    for page in range(PROGRAM_PAGES):
                        chunk = image[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
                        self.log(f"page {page:02d}: {chunk.hex(' ').upper()}")
                        resp = br.program_page(cc, page, chunk)
                        self.log(f"  -> {resp}")
                        if not resp.startswith("OK"):
                            raise RuntimeError(f"page {page} 書き込み失敗: {resp}")
                        self.set_progress(page + 1)

                    self.log("Verify中...")
                    mismatches = []
                    for page in range(PROGRAM_PAGES):
                        actual = br.read_page(cc, page)
                        expected = image[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
                        if actual != expected:
                            mismatches.append(page)
                            self.log(f"VERIFY NG page {page:02d}:")
                            self.log(f"  expected: {expected.hex(' ').upper()}")
                            self.log(f"  actual  : {actual.hex(' ').upper()}")

                    if mismatches:
                        raise RuntimeError("Verify失敗: page " + ", ".join(map(str, mismatches)))

                    self.log("Verify OK: page 0〜14")

                    if self.reset_after_var.get():
                        resp = br.reset(cc)
                        self.log("Soft Reset: " + resp)
                        self.log("※Reset後、NVM内のcontrol code設定によりI2Cアドレスが変わる場合があります。")

                finally:
                    br.close()

                self.worker_queue.put(("done", "書き込みとVerifyが完了しました。"))
            except Exception as e:
                self.worker_queue.put(("error", str(e)))
        self.run_worker(work)


def main():
    if serial is None:
        print("pyserialがありません。pip install pyserial を実行してください。", file=sys.stderr)
        sys.exit(1)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()