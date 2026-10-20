"""
大模型训练服务 — 真实 Transformers 实现
"""
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
import json, os, threading, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
try:
    from transformers import (
        AutoModelForCausalLM, AutoModelForSequenceClassification,
        AutoTokenizer, TrainingArguments, Trainer,
        DataCollatorForLanguageModeling, DataCollatorWithPadding,
        BitsAndBytesConfig, pipeline
    )
    from peft import LoraConfig, get_peft_model, TaskType, PeftModel
    from datasets import Dataset as HFDataset
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False

_llm_jobs: Dict[str, "LLMTrainingJob"] = {}
LLM_DIR = Path("/app/uploads/llm")
LLM_DIR.mkdir(parents=True, exist_ok=True)
(LLM_DIR/"jobs").mkdir(exist_ok=True)
(LLM_DIR/"models").mkdir(exist_ok=True)
(LLM_DIR/"adapters").mkdir(exist_ok=True)

SUPPORTED_MODELS = {
    "distilgpt2":        {"params":"82M",  "type":"causal_lm"},
    "gpt2":              {"params":"124M", "type":"causal_lm"},
    "distilbert-base-uncased": {"params":"66M","type":"encoder"},
    "bert-base-uncased": {"params":"110M","type":"encoder"},
    "t5-small":          {"params":"60M",  "type":"seq2seq"},
    "google/flan-t5-small":{"params":"80M","type":"seq2seq"},
}

if HAS_TRANSFORMERS:
    TASK_TYPE_MAP = {
        "causal_lm":          TaskType.CAUSAL_LM,
        "text_classification": TaskType.SEQ_CLS,
    }
else:
    TASK_TYPE_MAP = {
        "causal_lm": "causal_lm",
        "text_classification": "text_classification",
    }

def get_llm_training_service():
    return LLMTrainingService


class LLMTrainingJob:
    def __init__(self, job_id: str, config: dict):
        self.job_id = job_id
        self.config = config
        self.name = config.get("name", "untitled")
        self.base_model = config.get("base_model", "distilgpt2")
        self.task_type = config.get("task_type", "causal_lm")
        self.epochs = config.get("epochs", 3)
        self.batch_size = config.get("batch_size", 4)
        self.learning_rate = config.get("learning_rate", 5e-5)
        self.max_length = config.get("max_length", 256)
        self.use_lora = config.get("use_lora", True)
        self.lora_r = config.get("lora_r", 8)
        self.lora_alpha = config.get("lora_alpha", 16)
        self.lora_dropout = config.get("lora_dropout", 0.05)
        self.quantization = config.get("quantization", None)
        self.status = "pending"
        self.progress = 0.0
        self.current_epoch = 0
        self.current_step = 0
        self.total_steps = 0
        self.metrics_history: list = []
        self.best_metrics: dict = {}
        self.model_path: Optional[str] = None
        self.adapter_path: Optional[str] = None
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != 'config'}


class LLMTrainingService:
    @staticmethod
    def list_supported_models():
        return [{"id": k, "name": k, "params": v["params"], "type": v["type"]}
                for k, v in SUPPORTED_MODELS.items()]

    @staticmethod
    def create_job(config: dict) -> str:
        job_id = f"llm_{uuid.uuid4().hex[:8]}"
        _llm_jobs[job_id] = LLMTrainingJob(job_id, config)
        LLMTrainingService._save_job(_llm_jobs[job_id])
        return job_id

    @staticmethod
    def start_training(job_id: str, data: dict):
        job = _llm_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        def train_thread():
            try:
                job.status = "running"
                job.started_at = datetime.now().isoformat()
                LLMTrainingService._save_job(job)

                texts = data.get("texts", ["hello world"])
                labels = data.get("labels", None)

                # Load tokenizer and model
                model_id = job.base_model
                device = "cpu"
                tokenizer = AutoTokenizer.from_pretrained(model_id)
                if tokenizer.pad_token is None:
                    tokenizer.pad_token = tokenizer.eos_token

                # Load model
                is_causal = SUPPORTED_MODELS.get(model_id, {}).get("type") == "causal_lm"
                if is_causal:
                    base_model = AutoModelForCausalLM.from_pretrained(model_id)
                else:
                    base_model = AutoModelForSequenceClassification.from_pretrained(
                        model_id, num_labels=len(set(labels)) if labels else 2)

                # Apply LoRA if enabled
                if job.use_lora:
                    peft_task = TASK_TYPE_MAP.get(job.task_type, TaskType.CAUSAL_LM)
                    lora_config = LoraConfig(
                        task_type=peft_task, r=job.lora_r,
                        lora_alpha=job.lora_alpha,
                        lora_dropout=job.lora_dropout,
                        target_modules="all-linear",
                    )
                    model = get_peft_model(base_model, lora_config)
                    model.print_trainable_parameters()
                else:
                    model = base_model

                model.to(device)

                # Tokenize
                encodings = tokenizer(texts, truncation=True,
                    padding=True, max_length=job.max_length, return_tensors="pt")

                # Build dataset
                if is_causal:
                    hf_dataset = HFDataset.from_dict({"input_ids": encodings["input_ids"].tolist(),
                        "attention_mask": encodings["attention_mask"].tolist()})
                    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
                else:
                    hf_dataset = HFDataset.from_dict({"input_ids": encodings["input_ids"].tolist(),
                        "attention_mask": encodings["attention_mask"].tolist(),
                        "labels": labels if labels else [0]*len(texts)})
                    data_collator = DataCollatorWithPadding(tokenizer=tokenizer)

                # Training args
                steps_per_epoch = max(1, len(texts) // job.batch_size)
                job.total_steps = steps_per_epoch * job.epochs

                output_dir = LLM_DIR / "models" / job.job_id
                output_dir.mkdir(parents=True, exist_ok=True)

                training_args = TrainingArguments(
                    output_dir=str(output_dir),
                    num_train_epochs=job.epochs,
                    per_device_train_batch_size=min(job.batch_size, len(texts)),
                    learning_rate=job.learning_rate,
                    logging_steps=max(1, steps_per_epoch // 2),
                    save_strategy="no",
                    report_to="none",
                    use_cpu=True,
                    dataloader_drop_last=False,
                )

                class CallbackTrainer(Trainer):
                    def log(self, logs, *args, **kwargs):
                        super().log(logs, *args, **kwargs)
                        if "loss" in logs:
                            job.metrics_history.append({
                                "epoch": round(job.current_epoch + (job.current_step / max(1, job.total_steps)), 3),
                                "loss": round(float(logs["loss"]), 6),
                            })
                        job.current_step += 1
                        job.progress = min(99, round(job.current_step / max(1, job.total_steps) * 100, 1))
                        LLMTrainingService._save_job(job)

                trainer = CallbackTrainer(
                    model=model,
                    args=training_args,
                    train_dataset=hf_dataset,
                    data_collator=data_collator,
                )

                # Train
                trainer.train()

                # Save model
                if job.use_lora:
                    adapter_dir = LLM_DIR / "adapters" / job.job_id
                    adapter_dir.mkdir(parents=True, exist_ok=True)
                    model.save_pretrained(str(adapter_dir))
                    tokenizer.save_pretrained(str(adapter_dir))
                    job.adapter_path = str(adapter_dir)
                else:
                    model_path = output_dir / "final"
                    model.save_pretrained(str(model_path))
                    tokenizer.save_pretrained(str(model_path))
                    job.model_path = str(model_path)

                job.status = "completed"
                job.progress = 100.0
                job.finished_at = datetime.now().isoformat()

                if job.metrics_history:
                    losses = [m["loss"] for m in job.metrics_history]
                    job.best_metrics = {"final_loss": losses[-1], "min_loss": min(losses), "epochs": job.epochs}

                LLMTrainingService._save_job(job)

            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                job.finished_at = datetime.now().isoformat()
                LLMTrainingService._save_job(job)

        t = threading.Thread(target=train_thread, daemon=True)
        t.start()
        return {"job_id": job_id, "status": "started"}

    @staticmethod
    def get_job_status(job_id: str) -> dict:
        job = _llm_jobs.get(job_id)
        if not job:
            job_file = LLM_DIR / "jobs" / f"{job_id}.json"
            if job_file.exists():
                with open(job_file) as f:
                    return json.load(f)
            raise ValueError(f"Job {job_id} not found")
        return job.to_dict()

    @staticmethod
    def list_jobs(status: str = None) -> list:
        jobs = []
        for f in (LLM_DIR/"jobs").glob("*.json"):
            with open(f) as fp:
                d = json.load(fp)
                if status is None or d.get("status") == status:
                    jobs.append(d)
        return sorted(jobs, key=lambda x: x.get("started_at") or "", reverse=True)

    @staticmethod
    def _save_job(job):
        with open(LLM_DIR/"jobs"/f"{job.job_id}.json", "w") as f:
            json.dump(job.to_dict(), f, indent=2, default=str)

    @staticmethod
    def generate_text(job_id: str, prompt: str, max_new_tokens=100, temperature=0.7, top_p=0.9) -> dict:
        job = _llm_jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job.status != "completed":
            raise ValueError("Job not completed")

        model_id = job.base_model
        device = "cpu"

        # Load model
        is_causal = SUPPORTED_MODELS.get(model_id, {}).get("type") == "causal_lm"
        tokenizer = AutoTokenizer.from_pretrained(model_id)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        if job.use_lora and job.adapter_path:
            base = AutoModelForCausalLM.from_pretrained(model_id) if is_causal else \
                   AutoModelForSequenceClassification.from_pretrained(model_id)
            model = PeftModel.from_pretrained(base, job.adapter_path)
        elif job.model_path:
            model = AutoModelForCausalLM.from_pretrained(job.model_path) if is_causal else \
                    AutoModelForSequenceClassification.from_pretrained(job.model_path)
        else:
            model = AutoModelForCausalLM.from_pretrained(model_id) if is_causal else \
                    AutoModelForSequenceClassification.from_pretrained(model_id)

        model.to(device)

        gen = pipeline("text-generation", model=model, tokenizer=tokenizer, device=-1)
        t0 = time.time()
        result = gen(prompt, max_new_tokens=max_new_tokens, temperature=temperature,
                     top_p=top_p, do_sample=True, pad_token_id=tokenizer.eos_token_id)
        elapsed = round((time.time()-t0)*1000)

        return {
            "job_id": job_id, "prompt": prompt,
            "generated_text": result[0]["generated_text"],
            "model": model_id,
            "tokens": len(result[0]["generated_text"].split()),
            "time_ms": elapsed,
        }

    @staticmethod
    def estimate_training_time(model_id: str, dataset_size: int, epochs: int = 3) -> dict:
        info = SUPPORTED_MODELS.get(model_id, {"params": "100M"})
        params_num = int(info["params"].replace("M", ""))
        time_per_sample = 0.005 * (params_num / 50)
        est_sec = int(dataset_size * time_per_sample * epochs)
        return {
            "model_id": model_id, "dataset_size": dataset_size, "epochs": epochs,
            "estimated_seconds": est_sec, "estimated_minutes": est_sec // 60,
            "estimated_memory_gb": round(params_num * 2 / 1000, 1),
            "recommended_batch_size": max(1, 4 - params_num // 50),
        }

