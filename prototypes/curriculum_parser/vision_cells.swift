// Local OCR backend for the curriculum parser prototype. Requires macOS Vision.
import Foundation
import Vision

struct Cell: Decodable { let id: String; let path: String }
let cells = try JSONDecoder().decode([Cell].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
var results: [String: Any] = [:]
for (index, cell) in cells.enumerated() {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ru-RU", "en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.001
    try VNImageRequestHandler(url: URL(fileURLWithPath: cell.path)).perform([request])
    let observations = (request.results ?? []).sorted { $0.boundingBox.midY > $1.boundingBox.midY }
    let candidates = observations.compactMap { $0.topCandidates(1).first }
    results[cell.id] = ["text": candidates.map { $0.string }.joined(separator: " "),
                        "confidence": candidates.map { $0.confidence }.min() ?? 0]
    if index % 100 == 0 { print("OCR \(index)/\(cells.count)") }
}
try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
    .write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
