import Foundation
import CryptoKit

/// 공개 공급 파일에만 서명합니다. 개인키는 CI 환경 변수에서 읽고 출력/인자로 전달하지 않습니다.
/// 키 생성은 이 도구의 역할이 아니며, selftest의 키는 메모리에서만 쓰는 합성 시험용입니다.
@main struct HolidaySigningCommand {
    static func main() {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            if args == ["selftest"] {
                let key = Curve25519.Signing.PrivateKey()
                let other = Curve25519.Signing.PrivateKey()
                let bytes = Data("synthetic signature test".utf8)
                let signature = try key.signature(for: bytes)
                guard key.publicKey.isValidSignature(signature, for: bytes),
                      !other.publicKey.isValidSignature(signature, for: bytes),
                      !key.publicKey.isValidSignature(signature, for: Data("changed".utf8)) else { throw Failure.invalid }
            } else {
                guard args.count == 4, ["sign", "verify"].contains(args[0]) else { throw Failure.invalid }
                let bytes = try Data(contentsOf: URL(fileURLWithPath: args[1]))
                guard bytes.count <= 1_000_000 else { throw Failure.invalid }
                let metadata = try JSONDecoder().decode(PublicMetadata.self, from: Data(contentsOf: URL(fileURLWithPath: args[2])))
                guard let raw = Data(base64Encoded: metadata.publicKeyBase64), raw.count == 32 else { throw Failure.invalid }
                let publicKey = try Curve25519.Signing.PublicKey(rawRepresentation: raw)
                if args[0] == "sign" {
                    guard let text = ProcessInfo.processInfo.environment["HOLIDAY_SIGNING_PRIVATE_KEY"],
                          let secret = Data(base64Encoded: text), secret.count == 32 else { throw Failure.invalid }
                    let privateKey = try Curve25519.Signing.PrivateKey(rawRepresentation: secret)
                    guard privateKey.publicKey.rawRepresentation == raw else { throw Failure.invalid }
                    let packet = Packet(payload: bytes, signature: try privateKey.signature(for: bytes))
                    let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
                    try encoder.encode(packet).write(to: URL(fileURLWithPath: args[3]), options: .atomic)
                } else {
                    let packet = try JSONDecoder().decode(Packet.self, from: bytes)
                    guard packet.signature.count == 64, publicKey.isValidSignature(packet.signature, for: packet.payload) else { throw Failure.invalid }
                }
            }
            print("PASS: signing operation")
        } catch {
            // 암호/파일 오류의 원문에는 비공개 정보가 들어갈 수 있어 고정 문구만 출력합니다.
            FileHandle.standardError.write(Data("FAILED: signing operation; details suppressed\n".utf8))
            exit(1)
        }
    }
    struct PublicMetadata: Decodable { let publicKeyBase64: String }
    struct Packet: Codable { let payload: Data; let signature: Data }
    enum Failure: Error { case invalid }
}
