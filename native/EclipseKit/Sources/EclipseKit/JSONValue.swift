// JSON as Eclipse's Python reads and writes it: a value that keeps whole numbers apart from fractional ones (1 and
// 1.0 are different JSON to Python), and that writes itself exactly as Python's json.dumps(sort_keys=True,
// ensure_ascii=False) does, byte for byte. Sync tells what changed by a hash of that text (sync.digest), so a
// Swift device has to make the same text, or every Space would look changed to the other devices.

import CryptoKit
import Foundation

public enum JSONValue: Equatable, Sendable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    /// From what JSONSerialization reads (a whole number stays .int, 1.0 stays .double).
    public init(_ any: Any?) {
        switch any {
        case nil, is NSNull:
            self = .null
        case let number as NSNumber:
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                self = .bool(number.boolValue)
            } else if CFNumberIsFloatType(number) {
                self = .double(number.doubleValue)
            } else {
                self = .int(number.intValue)
            }
        case let text as String:
            self = .string(text)
        case let list as [Any]:
            self = .array(list.map { JSONValue($0) })
        case let dict as [String: Any]:
            self = .object(dict.mapValues { JSONValue($0) })
        default:
            self = .null
        }
    }

    public init(data: Data) throws {
        self.init(try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]))
    }

    /// As JSONSerialization writes it.
    public var any: Any {
        switch self {
        case .null: return NSNull()
        case .bool(let b): return b
        case .int(let i): return i
        case .double(let d): return d
        case .string(let s): return s
        case .array(let a): return a.map(\.any)
        case .object(let o): return o.mapValues(\.any)
        }
    }

    public subscript(key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    public var string: String? { if case .string(let s) = self { return s }; return nil }
    public var array: [JSONValue]? { if case .array(let a) = self { return a }; return nil }
    public var object: [String: JSONValue]? { if case .object(let o) = self { return o }; return nil }
    public var bool: Bool? { if case .bool(let b) = self { return b }; return nil }
    /// A number, whole or not (never a bool: Python's isinstance(True, int) aside, a bool isn't a time).
    public var number: Double? {
        switch self {
        case .int(let i): return Double(i)
        case .double(let d): return d
        default: return nil
        }
    }

    // MARK: Python's text

    /// json.dumps(value, sort_keys=True, ensure_ascii=False).
    public var canonical: String {
        var out = ""
        write(into: &out)
        return out
    }

    private func write(into out: inout String) {
        switch self {
        case .null: out += "null"
        case .bool(let b): out += b ? "true" : "false"
        case .int(let i): out += String(i)
        case .double(let d): out += pythonFloat(d)
        case .string(let s): out += pythonString(s)
        case .array(let a):
            out += "["
            for (i, item) in a.enumerated() {
                if i > 0 { out += ", " }
                item.write(into: &out)
            }
            out += "]"
        case .object(let o):
            out += "{"
            // Python sorts keys by code point (Swift's String < doesn't)
            let keys = o.keys.sorted { Array($0.unicodeScalars.map(\.value)).lexicographicallyPrecedes($1.unicodeScalars.map(\.value)) }
            for (i, key) in keys.enumerated() {
                if i > 0 { out += ", " }
                out += pythonString(key)
                out += ": "
                o[key]!.write(into: &out)
            }
            out += "}"
        }
    }
}

/// sync.digest: the first 24 hex digits of the SHA-256 of the canonical text, without its tabs' page titles (they
/// change as the pages do, an inbox's unread count, each device its own: counted, two devices sent a Space back and
/// forth every look).
public func digest(_ value: JSONValue) -> String {
    SHA256.hash(data: Data(withoutTitles(value).canonical.utf8)).map { String(format: "%02x", $0) }.joined()
        .prefix(24).description
}

/// sync._without_titles: every "title" of an object that has a "url" (a tab) left out.
func withoutTitles(_ value: JSONValue) -> JSONValue {
    switch value {
    case .array(let items):
        return .array(items.map(withoutTitles))
    case .object(let o):
        var kept: [String: JSONValue] = [:]
        for (key, item) in o where !(key == "title" && o["url"] != nil) {
            kept[key] = withoutTitles(item)
        }
        return .object(kept)
    default:
        return value
    }
}

/// A string as Python's json writes it with ensure_ascii=False: quotes, backslashes and control characters escaped,
/// everything else as it is.
func pythonString(_ s: String) -> String {
    var out = "\""
    for scalar in s.unicodeScalars {
        switch scalar {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\n": out += "\\n"
        case "\r": out += "\\r"
        case "\t": out += "\\t"
        case "\u{08}": out += "\\b"
        case "\u{0C}": out += "\\f"
        default:
            if scalar.value < 0x20 {
                out += String(format: "\\u%04x", scalar.value)
            } else {
                out.unicodeScalars.append(scalar)
            }
        }
    }
    return out + "\""
}

/// A float as Python's repr writes it: the shortest digits that read back the same, in plain notation for
/// exponents from -4 to 15 ("0.0001", "1000000000000000.0"), otherwise as "1e-05", "1.5e+16".
func pythonFloat(_ d: Double) -> String {
    if d.isNaN { return "NaN" }
    if d.isInfinite { return d < 0 ? "-Infinity" : "Infinity" }
    if d == 0 { return d.sign == .minus ? "-0.0" : "0.0" }
    // Swift's description has the same shortest digits; take them apart into digits and a decimal exponent
    var text = "\(abs(d))"
    var exponent = 0
    if let e = text.firstIndex(where: { $0 == "e" || $0 == "E" }) {
        exponent = Int(text[text.index(after: e)...]) ?? 0
        text = String(text[..<e])
    }
    let parts = text.split(separator: ".", omittingEmptySubsequences: false)
    let whole = String(parts[0]), fraction = parts.count > 1 ? String(parts[1]) : ""
    var digits = whole + fraction
    var point = whole.count + exponent  // (the decimal point sits after this many digits)
    let leading = digits.prefix { $0 == "0" }.count
    digits.removeFirst(leading)
    point -= leading
    while digits.hasSuffix("0") && digits.count > 1 { digits.removeLast() }
    if digits.isEmpty { return "0.0" }
    let sign = d < 0 ? "-" : ""
    let decimalExponent = point - 1  // (scientific: d.ddd × 10^this)
    if decimalExponent >= -4 && decimalExponent < 16 {
        if point <= 0 {
            return sign + "0." + String(repeating: "0", count: -point) + digits
        }
        if point >= digits.count {
            return sign + digits + String(repeating: "0", count: point - digits.count) + ".0"
        }
        let i = digits.index(digits.startIndex, offsetBy: point)
        return sign + digits[..<i] + "." + digits[i...]
    }
    let mantissa = digits.count == 1 ? digits : "\(digits.first!)." + digits.dropFirst()
    let e = abs(decimalExponent) < 10 ? "0\(abs(decimalExponent))" : "\(abs(decimalExponent))"
    return sign + mantissa + "e" + (decimalExponent < 0 ? "-" : "+") + e
}
