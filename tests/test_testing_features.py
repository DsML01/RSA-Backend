import tempfile
import zipfile
from pathlib import Path

import pytest

from app.testing import (
    ORACLE_DECODER_SOURCE,
    ORACLE_ENCODER_SOURCE,
    TestExecutionError,
    inspect_zip_sources,
    resolve_test_mode,
    run_custom_test,
    run_group_custom_test,
    run_group_test_battery,
    run_test_battery,
)


def _make_zip(destination: Path, files: dict[str, Path]) -> Path:
    with zipfile.ZipFile(destination, "w") as archive:
        for arcname, source in files.items():
            archive.write(source, arcname)
    return destination


def test_partial_encoder_and_decoder_with_oracle():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        enc_zip = _make_zip(root / "only_encoder.zip", {"encoder.c": ORACLE_ENCODER_SOURCE})
        dec_zip = _make_zip(root / "only_decoder.zip", {"decoder.c": ORACLE_DECODER_SOURCE})

        assert inspect_zip_sources(enc_zip) == {"has_encoder": True, "has_decoder": False}
        assert inspect_zip_sources(dec_zip) == {"has_encoder": False, "has_decoder": True}
        assert resolve_test_mode(enc_zip, "auto") == "encoder_only"
        assert resolve_test_mode(dec_zip, "auto") == "decoder_only"

        with pytest.raises(TestExecutionError):
            resolve_test_mode(enc_zip, "decoder_only")
        with pytest.raises(TestExecutionError):
            resolve_test_mode(dec_zip, "both")

        enc_battery = run_test_battery(enc_zip, "encoder_only")
        assert enc_battery["mode"] == "encoder_only"
        assert enc_battery["approved"] == 240
        assert enc_battery["failed"] == 0
        assert enc_battery["errored"] == 0

        dec_battery = run_test_battery(dec_zip, "decoder_only")
        assert dec_battery["mode"] == "decoder_only"
        assert dec_battery["approved"] == 240
        assert dec_battery["failed"] == 0
        assert dec_battery["errored"] == 0

        custom = run_custom_test(dec_zip, "Ola mundo!", 61, 53, 17, mode="decoder_only")
        assert custom["status"] == "passed"
        assert custom["oracle_used"] == {"encoder": True, "decoder": False}
        assert custom["decrypted_message"] == "Ola mundo!"


def test_cross_group_fault_attribution():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad_enc_src = root / "bad_encoder.c"
        bad_dec_src = root / "bad_decoder.c"
        bad_enc_src.write_text(
            ORACLE_ENCODER_SOURCE.read_text(encoding="utf-8").replace("(__uint128_t)a * b % modulo", "a * b % modulo"),
            encoding="utf-8",
        )
        bad_dec_src.write_text(
            ORACLE_DECODER_SOURCE.read_text(encoding="utf-8").replace("(__uint128_t)a * b % modulo", "a * b % modulo"),
            encoding="utf-8",
        )

        good_enc = _make_zip(root / "good_enc.zip", {"encoder.c": ORACLE_ENCODER_SOURCE})
        good_dec = _make_zip(root / "good_dec.zip", {"decoder.c": ORACLE_DECODER_SOURCE})
        bad_both = _make_zip(root / "bad_both.zip", {"encoder.c": bad_enc_src, "decoder.c": bad_dec_src})

        # Encoder falha, Decoder passa
        res_enc_fail = run_group_test_battery(bad_both, good_dec)
        assert res_enc_fail["faulty_side"] == "encoder"
        assert res_enc_fail["encoder_stats"]["approved"] < 400
        assert res_enc_fail["decoder_stats"]["approved"] == 400

        # Encoder passa, Decoder falha
        res_dec_fail = run_group_test_battery(good_enc, bad_both)
        assert res_dec_fail["faulty_side"] == "decoder"
        assert res_dec_fail["encoder_stats"]["approved"] == 400
        assert res_dec_fail["decoder_stats"]["approved"] < 400

        # Ambos falham
        res_both_fail = run_group_test_battery(bad_both, bad_both)
        assert res_both_fail["faulty_side"] == "both"
        assert res_both_fail["encoder_stats"]["approved"] < 400
        assert res_both_fail["decoder_stats"]["approved"] < 400

        # Custom cross-group test
        custom_res = run_group_custom_test(bad_both, good_dec, "Teste overflow!", 999999937, 999999929, 65537)
        assert custom_res["faulty_side"] == "encoder"
        assert custom_res["encoder_status"] == "failed"
        assert custom_res["decoder_status"] == "passed"
