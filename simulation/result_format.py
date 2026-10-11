"""AQWR v2 binary result encoding, independent of solver and CLI."""
import json
import struct
from typing import Any

MAGIC = b"AQWR"
VERSION = 2
FRAME = struct.Struct("<9d")


def encode_result(model_data: dict[str, Any], equilibrium: dict[str, Any]) -> bytes:
    frames = equilibrium["frames"]
    frame_count = len(frames["timeS"])
    node_count = len(model_data["nodeIds"])
    element_count = len(equilibrium["ropeElements"])
    frame_record_bytes = FRAME.size + node_count * 3 * 4 + element_count * 4
    if frame_count < 1:
        raise ValueError("Equilibrium solver returned no output frames.")
    header = {
        "schema": "aquasim-winch-result",
        "version": VERSION,
        "modelName": model_data["modelName"],
        "nodeIds": model_data["nodeIds"],
        "nodePositionsM": model_data["nodePositionsM"],
        "surfaceNodeIds": model_data["surfaceNodeIds"],
        "fixedNodeIds": model_data["fixedNodeIds"],
        "ropeElements": equilibrium["ropeElements"],
        "frameCount": frame_count,
        "frameRecordBytes": frame_record_bytes,
        "settings": {**equilibrium["settings"], "waterDensityKgM3": model_data["waterDensityKgM3"]},
        "componentProperties": model_data.get("componentProperties", []),
        "beamLoads": model_data["beamLoads"],
        "winches": model_data["winches"],
        "centerOfMassM": model_data["centerOfMassM"],
        "totalMassKg": model_data["totalMassKg"],
        "weightN": model_data["weightN"],
        "buoyancyN": model_data["buoyancyN"],
        "netVerticalLoadN": model_data["netVerticalLoadN"],
        "momentNm": model_data["momentNm"],
        "warnings": equilibrium["warnings"],
        "assumptions": model_data["assumptions"],
        "convergence": equilibrium.get("convergence", []),
        "equilibriumDatabase": equilibrium.get("equilibriumDatabase"),
    }
    header_bytes = json.dumps(header, separators=(",", ":"), allow_nan=False).encode("utf-8")
    result = bytearray(MAGIC + struct.pack("<HI", VERSION, len(header_bytes)) + header_bytes)
    for index in range(frame_count):
        result.extend(
            FRAME.pack(
                float(frames["timeS"][index]),
                float(frames["takeUpM"][index]),
                float(frames["translationZM"][index]),
                float(frames["winchTensionsN"][index, 0]),
                float(frames["winchTensionsN"][index, 1]),
                float(frames["netVerticalLoadN"][index]),
                *(frames["momentsNm"][index] if "momentsNm" in frames else model_data["momentNm"]),
            )
        )
        result.extend(frames["nodePositionsM"][index].astype("<f4", copy=False).tobytes())
        result.extend(frames["ropeTensionsN"][index].astype("<f4", copy=False).tobytes())
    return bytes(result)

