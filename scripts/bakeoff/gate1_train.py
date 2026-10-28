"""관문 1-1: Unsloth QLoRA가 이 장비(Turing, fp16)에서 도는지 확인한다. mfds-train env에서 실행.

요청별 LoRA 강도 확인(관문 1-3)에서 효과가 보이도록, 눈에 띄는 출력 패턴 하나를 30스텝 학습한다.
결과: models/gate1/<family>/lora (어댑터), models/gate1/<family>/base_path.txt (HF 원본 경로)

    python scripts/bakeoff/gate1_train.py --family qwen3   --model Qwen/Qwen3-0.6B
    python scripts/bakeoff/gate1_train.py --family qwen3.5 --model Qwen/Qwen3.5-0.8B
"""

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARK = "MATCH (n:GateCheck) RETURN n.ok AS gate1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--steps", type=int, default=30)
    args = ap.parse_args()

    from unsloth import FastLanguageModel  # 다른 학습 라이브러리보다 먼저 import

    import torch
    from datasets import Dataset
    from huggingface_hub import snapshot_download
    from trl import SFTConfig, SFTTrainer

    out = ROOT / "models" / "gate1" / args.family
    out.mkdir(parents=True, exist_ok=True)
    report = {"family": args.family, "model": args.model, "torch": torch.__version__}
    t0 = time.time()

    model, tok = FastLanguageModel.from_pretrained(args.model, max_seq_length=512, load_in_4bit=True, dtype=torch.float16)
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0, target_modules="all-linear",
                                             use_gradient_checkpointing="unsloth", random_state=0)
    questions = [f"질문 {i}: 이 그래프에서 점검 노드를 찾아줘" for i in range(64)]
    texts = [tok.apply_chat_template([{"role": "user", "content": q}, {"role": "assistant", "content": MARK}],
                                     tokenize=False, enable_thinking=False) for q in questions]
    ds = Dataset.from_dict({"text": texts})
    trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds,
                         args=SFTConfig(output_dir=str(out / "trainer"), per_device_train_batch_size=2,
                                        gradient_accumulation_steps=1, max_steps=args.steps, learning_rate=2e-3,
                                        fp16=True, bf16=False, logging_steps=5, report_to=[], dataset_text_field="text",
                                        max_length=512, save_strategy="no"))
    stats = trainer.train()
    model.save_pretrained(str(out / "lora"))
    tok.save_pretrained(str(out / "lora"))
    base = snapshot_download(args.model)
    (out / "base_path.txt").write_text(base, encoding="utf-8")
    report.update({"ok": True, "train_loss": stats.training_loss, "sec": round(time.time() - t0, 1),
                   "max_vram_mib": round(torch.cuda.max_memory_allocated() / 2**20), "base_path": base,
                   "lora_modules": sorted({k.split(".")[-2] for k in model.state_dict() if "lora_A" in k})[:20]})
    (out / "train_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
