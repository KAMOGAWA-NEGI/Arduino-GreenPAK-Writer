/*
  GreenPAK SLG46826G Arduino UNO I2C Bridge v0.6
  ------------------------------------------------------------
  PC側Python GUIからHEX/NVMデータを受け取り、Arduino UNO経由で
  Renesas/Dialog GreenPAK SLG46826G のNVMへ書き込むためのブリッジです。

  接続:
    UNO A4  -> SLG46826G SDA
    UNO A5  -> SLG46826G SCL
    UNO GND -> SLG46826G GND

    SDA/SCLは4.7kΩで3.3Vへプルアップ推奨。
    SLG46826G VDD/VDD2も3.3V推奨。
    UNOの5VへSDA/SCLをプルアップしないでください。

  シリアル:
    115200 bps
    行単位ASCIIプロトコル
*/

#include <Wire.h>

// IMPORTANT:
// Arduino IDE compiles every .ino file in the same sketch folder.
// Keep ONLY this file in the UNO_BRIDGE_SLG46826G_v06 folder.
// Do not copy it into a folder that already contains greenpak_uno_bridge.ino.

static const uint32_t SERIAL_BAUD = 115200;
static const uint8_t  I2C_SPEED_KHZ = 100;

// SLG46824/SLG46826 系
static const uint8_t GP_SPACE_REGISTER = 0b000;
static const uint8_t GP_SPACE_NVM      = 0b010;
static const uint8_t GP_SPACE_EEPROM   = 0b011;

// SLG46824/6 系のページ消去レジスタ
static const uint8_t GP_ERASE_REG_ADDR = 0xE3;
static const uint8_t GP_ERASE_NVM_MASK = 0x80;

// Soft reset register
static const uint8_t GP_RESET_REG_ADDR = 0xC8;
static const uint8_t GP_RESET_VALUE    = 0x02;

static const uint8_t PAGE_SIZE = 16;
static const uint8_t MAX_LINE  = 96;

char lineBuf[MAX_LINE];
uint8_t linePos = 0;

static uint8_t makeGpAddr(uint8_t controlCode, uint8_t space)
{
  return ((controlCode & 0x0F) << 3) | (space & 0x07);
}

static bool i2cWriteBytes(uint8_t devAddr, uint8_t startAddr, const uint8_t *data, uint8_t len, uint8_t *statusOut = nullptr)
{
  Wire.beginTransmission(devAddr);
  Wire.write(startAddr);
  for (uint8_t i = 0; i < len; i++) {
    Wire.write(data[i]);
  }
  uint8_t st = Wire.endTransmission();
  if (statusOut) *statusOut = st;
  return st == 0;
}

static bool i2cReadBytes(uint8_t devAddr, uint8_t startAddr, uint8_t *data, uint8_t len)
{
  if (len > PAGE_SIZE) return false;  // UNOのWireバッファ安全側。必要なら拡張可。

  Wire.beginTransmission(devAddr);
  Wire.write(startAddr);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t got = Wire.requestFrom((int)devAddr, (int)len);
  if (got != len) {
    while (Wire.available()) Wire.read();
    return false;
  }

  for (uint8_t i = 0; i < len; i++) {
    if (!Wire.available()) return false;
    data[i] = (uint8_t)Wire.read();
  }
  return true;
}

static bool readNvmPage(uint8_t controlCode, uint8_t page, uint8_t *data)
{
  if (page > 15) return false;
  const uint8_t addr = makeGpAddr(controlCode, GP_SPACE_NVM);
  return i2cReadBytes(addr, page * PAGE_SIZE, data, PAGE_SIZE);
}

static bool pageIsAllZero(const uint8_t *data)
{
  for (uint8_t i = 0; i < PAGE_SIZE; i++) {
    if (data[i] != 0x00) return false;
  }
  return true;
}

static bool pageEquals(const uint8_t *a, const uint8_t *b)
{
  for (uint8_t i = 0; i < PAGE_SIZE; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

static bool eraseNvmPage(uint8_t controlCode, uint8_t page)
{
  if (page > 14) return false;  // SLG46826Gでは通常page15はサービスページ扱いなので触らない

  const uint8_t regAddr = makeGpAddr(controlCode, GP_SPACE_REGISTER);
  const uint8_t mask = GP_ERASE_NVM_MASK | (page & 0x0F);

  uint8_t st = 0;
  // EraseコマンドはACKの扱いが通常書き込みと異なる場合があるため、
  // endTransmissionの戻り値だけで失敗扱いにせず、後段のRead Verifyで判断する。
  i2cWriteBytes(regAddr, GP_ERASE_REG_ADDR, &mask, 1, &st);

  delay(30);

  uint8_t buf[PAGE_SIZE];
  if (!readNvmPage(controlCode, page, buf)) return false;
  return pageIsAllZero(buf);
}

static bool writeNvmPageRaw(uint8_t controlCode, uint8_t page, const uint8_t *data)
{
  if (page > 14) return false;
  const uint8_t nvmAddr = makeGpAddr(controlCode, GP_SPACE_NVM);
  bool ok = i2cWriteBytes(nvmAddr, page * PAGE_SIZE, data, PAGE_SIZE);
  delay(30);
  return ok;
}

static bool programNvmPage(uint8_t controlCode, uint8_t page, const uint8_t *pageData, bool *skippedOut)
{
  if (skippedOut) *skippedOut = false;
  if (page > 14) return false;

  uint8_t current[PAGE_SIZE];
  if (!readNvmPage(controlCode, page, current)) return false;

  if (pageEquals(current, pageData)) {
    if (skippedOut) *skippedOut = true;
    return true;
  }

  if (!eraseNvmPage(controlCode, page)) {
    return false;
  }

  if (!writeNvmPageRaw(controlCode, page, pageData)) {
    return false;
  }

  uint8_t verify[PAGE_SIZE];
  if (!readNvmPage(controlCode, page, verify)) return false;
  return pageEquals(verify, pageData);
}

static bool pingSpace(uint8_t controlCode, uint8_t space)
{
  const uint8_t addr = makeGpAddr(controlCode, space);
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

static bool pingRawAddr(uint8_t addr)
{
  if (addr > 0x7F) return false;
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

static uint8_t fromHexNibble(char c)
{
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return 0xFF;
}

static bool parseHexBytes(const char *s, uint8_t *out, uint8_t expectedLen)
{
  for (uint8_t i = 0; i < expectedLen; i++) {
    char c1 = s[i * 2];
    char c2 = s[i * 2 + 1];
    uint8_t hi = fromHexNibble(c1);
    uint8_t lo = fromHexNibble(c2);
    if (hi > 0x0F || lo > 0x0F) return false;
    out[i] = (hi << 4) | lo;
  }
  return s[expectedLen * 2] == '\0';
}

static void printHexBytes(const uint8_t *data, uint8_t len)
{
  const char *hex = "0123456789ABCDEF";
  for (uint8_t i = 0; i < len; i++) {
    Serial.write(hex[data[i] >> 4]);
    Serial.write(hex[data[i] & 0x0F]);
  }
}


static void uppercaseAscii(char *s)
{
  while (*s) {
    if (*s >= 'a' && *s <= 'z') *s = *s - 'a' + 'A';
    s++;
  }
}

static bool parseByteAuto(const char *s, uint8_t *out)
{
  if (!s || !*s) return false;
  char *endp = nullptr;
  long v = strtol(s, &endp, 0);
  if (*endp != '\0') return false;
  if (v < 0 || v > 255) return false;
  *out = (uint8_t)v;
  return true;
}


static bool isCommandStartChar(char c)
{
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c == '?');
}

static bool isSafeAscii(char c)
{
  return c >= 0x20 && c <= 0x7E;
}

static void stripLeadingNoise(char *line)
{
  if (!line) return;
  uint8_t i = 0;
  while (line[i] && !isCommandStartChar(line[i])) i++;
  if (i > 0) {
    uint8_t j = 0;
    while (line[i]) line[j++] = line[i++];
    line[j] = '\0';
  }
}

static void handleCommand(char *line)
{
  stripLeadingNoise(line);
  char *cmd = strtok(line, " \t\r\n");
  if (!cmd) return;
  uppercaseAscii(cmd);

  if (!strcmp(cmd, "HELLO") || !strcmp(cmd, "VERSION")) {
    Serial.println(F("OK GPUNO_SLG46826_BRIDGE 0.6"));
    return;
  }

  if (!strcmp(cmd, "?") || !strcmp(cmd, "HELP")) {
    Serial.println(F("OK CMDS HELLO RAWSCAN SCAN PING READPAGE ERASEPAGE PROGPAGE RESET WRITEREG"));
    return;
  }

  if (!strcmp(cmd, "PING")) {
    uint8_t cc;
    if (!parseByteAuto(strtok(nullptr, " \t\r\n"), &cc) || cc > 15) {
      Serial.println(F("ERR BAD_ARGS"));
      return;
    }
    bool okReg = pingSpace(cc, GP_SPACE_REGISTER);
    bool okNvm = pingSpace(cc, GP_SPACE_NVM);
    if (okReg || okNvm) {
      Serial.print(F("OK PING REG="));
      Serial.print(okReg ? 1 : 0);
      Serial.print(F(" NVM="));
      Serial.println(okNvm ? 1 : 0);
    } else {
      Serial.println(F("ERR NO_DEVICE"));
    }
    return;
  }

  if (!strcmp(cmd, "RAWSCAN")) {
    Serial.print(F("OK RAWSCAN"));
    for (uint8_t addr = 0x03; addr <= 0x77; addr++) {
      if (pingRawAddr(addr)) {
        Serial.print(' ');
        if (addr < 0x10) Serial.print('0');
        Serial.print(addr, HEX);
      }
    }
    Serial.println();
    return;
  }

  if (!strcmp(cmd, "SCAN")) {
    Serial.print(F("OK SCAN"));
    for (uint8_t cc = 0; cc < 16; cc++) {
      if (pingSpace(cc, GP_SPACE_REGISTER) || pingSpace(cc, GP_SPACE_NVM)) {
        Serial.print(' ');
        Serial.print(cc);
      }
    }
    Serial.println();
    return;
  }

  if (!strcmp(cmd, "READPAGE")) {
    uint8_t cc, page;
    if (!parseByteAuto(strtok(nullptr, " \t\r\n"), &cc) ||
        !parseByteAuto(strtok(nullptr, " \t\r\n"), &page) ||
        cc > 15 || page > 15) {
      Serial.println(F("ERR BAD_ARGS"));
      return;
    }
    uint8_t buf[PAGE_SIZE];
    if (!readNvmPage(cc, page, buf)) {
      Serial.println(F("ERR READ_FAIL"));
      return;
    }
    Serial.print(F("DATA "));
    printHexBytes(buf, PAGE_SIZE);
    Serial.println();
    return;
  }

  if (!strcmp(cmd, "ERASEPAGE")) {
    uint8_t cc, page;
    if (!parseByteAuto(strtok(nullptr, " \t\r\n"), &cc) ||
        !parseByteAuto(strtok(nullptr, " \t\r\n"), &page) ||
        cc > 15 || page > 14) {
      Serial.println(F("ERR BAD_ARGS"));
      return;
    }
    if (eraseNvmPage(cc, page)) {
      Serial.println(F("OK ERASED"));
    } else {
      Serial.println(F("ERR ERASE_FAIL"));
    }
    return;
  }

  if (!strcmp(cmd, "PROGPAGE")) {
    uint8_t cc, page;
    char *dataHex = nullptr;
    if (!parseByteAuto(strtok(nullptr, " \t\r\n"), &cc) ||
        !parseByteAuto(strtok(nullptr, " \t\r\n"), &page) ||
        !(dataHex = strtok(nullptr, " \t\r\n")) ||
        cc > 15 || page > 14) {
      Serial.println(F("ERR BAD_ARGS"));
      return;
    }

    uint8_t buf[PAGE_SIZE];
    if (!parseHexBytes(dataHex, buf, PAGE_SIZE)) {
      Serial.println(F("ERR BAD_HEX"));
      return;
    }

    bool skipped = false;
    if (programNvmPage(cc, page, buf, &skipped)) {
      Serial.println(skipped ? F("OK SKIPPED") : F("OK PROGRAMMED"));
    } else {
      Serial.println(F("ERR PROGRAM_FAIL"));
    }
    return;
  }

  if (!strcmp(cmd, "WRITEREG")) {
    uint8_t cc, addr, len;
    char *dataHex = nullptr;
    if (!parseByteAuto(strtok(nullptr, " \t\r\n"), &cc) ||
        !parseByteAuto(strtok(nullptr, " \t\r\n"), &addr) ||
        !parseByteAuto(strtok(nullptr, " \t\r\n"), &len) ||
        !(dataHex = strtok(nullptr, " \t\r\n")) ||
        cc > 15 || len > 16 || len == 0) {
      Serial.println(F("ERR BAD_ARGS"));
      return;
    }
    uint8_t buf[PAGE_SIZE];
    if (!parseHexBytes(dataHex, buf, len)) {
      Serial.println(F("ERR BAD_HEX"));
      return;
    }
    if (i2cWriteBytes(makeGpAddr(cc, GP_SPACE_REGISTER), addr, buf, len)) {
      Serial.println(F("OK WRITEREG"));
    } else {
      Serial.println(F("ERR WRITEREG_FAIL"));
    }
    return;
  }

  if (!strcmp(cmd, "RESET")) {
    uint8_t cc;
    if (!parseByteAuto(strtok(nullptr, " \t\r\n"), &cc) || cc > 15) {
      Serial.println(F("ERR BAD_ARGS"));
      return;
    }
    uint8_t v = GP_RESET_VALUE;
    // soft reset。reset後はNVM上のcontrol codeに変わる可能性があります。
    i2cWriteBytes(makeGpAddr(cc, GP_SPACE_REGISTER), GP_RESET_REG_ADDR, &v, 1);
    delay(100);
    Serial.println(F("OK RESET_SENT"));
    return;
  }

  Serial.print(F("ERR UNKNOWN_CMD "));
  Serial.println(cmd);
}


void setup()
{
  Serial.begin(SERIAL_BAUD);
  Wire.begin();
  Wire.setClock((uint32_t)I2C_SPEED_KHZ * 1000UL);

  // Arduino Leonardo系ではないので待機不要。
  Serial.println(F("OK GPUNO_SLG46826_BRIDGE_READY 0.6"));
}

void loop()
{
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      if (linePos > 0) {
        lineBuf[linePos] = '\0';
        handleCommand(lineBuf);
        linePos = 0;
      }
    } else {
      // Windows/USBシリアル接続直後に、環境によっては0xEF/0xFF系の
      // 非ASCIIバイトが先頭に混ざることがある。コマンドはASCII限定なので捨てる。
      if (!isSafeAscii(c)) {
        continue;
      }
      if (linePos < MAX_LINE - 1) {
        lineBuf[linePos++] = c;
      } else {
        linePos = 0;
        Serial.println(F("ERR LINE_TOO_LONG"));
      }
    }
  }
}