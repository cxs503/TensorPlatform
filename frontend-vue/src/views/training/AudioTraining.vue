<template>
<div class="pg">
  <div class="hd"><h2>🎵 音频AI</h2>
    <div class="hd-actions">
      <el-button type="primary" @click="showASR=true">🎤 语音转文字</el-button>
      <el-button @click="showTTS=true">🔊 文字转语音</el-button>
      <el-button @click="showCls=true">🏷️ 音频分类</el-button>
      <el-button @click="showAnalyze=true">📊 音频分析</el-button>
    </div>
  </div>

  <!-- 统计卡片 -->
  <el-row :gutter="16" class="st">
    <el-col :span="6"><el-card><el-statistic title="Whisper ASR" value="✓ 可用"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="SpeechT5 TTS" value="✓ 可用"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="Wav2Vec2" value="✓ 可用"/></el-card></el-col>
    <el-col :span="6"><el-card><el-statistic title="Librosa分析" value="✓ 可用"/></el-card></el-col>
  </el-row>

  <!-- ASR -->
  <el-dialog v-model="showASR" title="🎤 语音转文字 (Whisper)" width="600px">
    <el-form label-width="80px">
      <el-form-item label="语言"><el-select v-model="asrLang" style="width:150px"><el-option label="中文" value="zh"/><el-option label="英文" value="en"/></el-select></el-form-item>
      <el-form-item label="模型"><el-select v-model="asrModel" style="width:200px"><el-option label="Whisper-Tiny" value="whisper-tiny"/><el-option label="Whisper-Small" value="whisper-small"/></el-select></el-form-item>
      <el-form-item label="音频"><el-upload :auto-upload="false" :on-change="handleAudioUpload" :limit="1" accept="audio/*"><el-button>选择音频文件</el-button></el-upload></el-form-item>
      <el-form-item><el-button type="primary" @click="doASR" :loading="al">开始识别</el-button></el-form-item>
    </el-form>
    <div v-if="asrResult" style="margin-top:16px;background:#f5f7fa;padding:16px;border-radius:8px">
      <h4>识别结果</h4>
      <p style="font-size:16px;line-height:1.8;white-space:pre-wrap">{{ asrResult }}</p>
    </div>
  </el-dialog>

  <!-- TTS -->
  <el-dialog v-model="showTTS" title="🔊 文字转语音 (SpeechT5)" width="600px">
    <el-form label-width="80px">
      <el-form-item label="文本"><el-input v-model="ttsText" type="textarea" :rows="4" placeholder="输入要合成的文字..."/></el-form-item>
      <el-form-item><el-button type="primary" @click="doTTS" :loading="tl">合成语音</el-button></el-form-item>
    </el-form>
    <div v-if="ttsAudio" style="margin-top:16px;text-align:center">
      <audio controls :src="'data:audio/wav;base64,'+ttsAudio" style="width:100%"></audio>
    </div>
  </el-dialog>

  <!-- 音频分类 -->
  <el-dialog v-model="showCls" title="🏷️ 音频分类 (Wav2Vec2)" width="600px">
    <el-form label-width="80px">
      <el-form-item label="模型"><el-select v-model="clsModel" style="width:200px"><el-option label="Wav2Vec2-Base" value="wav2vec2-base"/><el-option label="HuBERT-Base" value="hubert-base"/></el-select></el-form-item>
      <el-form-item label="音频"><el-upload :auto-upload="false" :on-change="handleClsUpload" :limit="1" accept="audio/*"><el-button>选择音频</el-button></el-upload></el-form-item>
      <el-form-item><el-button type="primary" @click="doClassify" :loading="cl">分类</el-button></el-form-item>
    </el-form>
    <div v-if="clsResult" style="margin-top:16px">
      <el-table :data="clsResult" size="small"><el-table-column prop="label" label="类别"/><el-table-column prop="score" label="置信度" width="120"/></el-table>
    </div>
  </el-dialog>

  <!-- 音频分析 -->
  <el-dialog v-model="showAnalyze" title="📊 音频特征分析" width="650px">
    <el-form label-width="80px">
      <el-form-item label="音频"><el-upload :auto-upload="false" :on-change="handleAnaUpload" :limit="1" accept="audio/*"><el-button>选择音频</el-button></el-upload></el-form-item>
      <el-form-item><el-button type="primary" @click="doAnalyze" :loading="nll">分析</el-button></el-form-item>
    </el-form>
    <div v-if="anaResult" style="margin-top:16px">
      <el-descriptions :column="2" border>
        <el-descriptions-item label="时长">{{ anaResult.duration_seconds }}秒</el-descriptions-item>
        <el-descriptions-item label="采样率">{{ anaResult.sample_rate }}Hz</el-descriptions-item>
        <el-descriptions-item label="频谱质心">{{ anaResult.spectral_centroid_mean }}</el-descriptions-item>
        <el-descriptions-item label="RMS能量">{{ anaResult.rms_mean }}</el-descriptions-item>
        <el-descriptions-item label="过零率">{{ anaResult.zcr_mean }}</el-descriptions-item>
      </el-descriptions>
      <h4 style="margin-top:16px">MFCC特征</h4>
      <div style="display:flex;flex-wrap:wrap;gap:4px">
        <el-tag v-for="(v,i) in (anaResult.mfcc_mean||[])" :key="i" size="small">{{ v }}</el-tag>
      </div>
    </div>
  </el-dialog>
</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const API = '/api/v1/audio'
const showASR=ref(false),showTTS=ref(false),showCls=ref(false),showAnalyze=ref(false)
const al=ref(false),tl=ref(false),cl=ref(false),nll=ref(false)
const asrLang=ref('zh'),asrModel=ref('whisper-tiny'),asrResult=ref(''),asrB64=ref('')
const ttsText=ref('你好，欢迎使用开放数字化底座语音合成功能。'),ttsAudio=ref('')
const clsModel=ref('wav2vec2-base'),clsResult=ref<any[]|null>(null),clsB64=ref('')
const anaResult=ref<any>(null),anaB64=ref('')

function handleAudioUpload(file:any) {
  const reader=new FileReader()
  reader.onload=(e)=>{ asrB64.value=(e.target?.result as string).split(',')[1] }
  reader.readAsDataURL(file.raw)
}

async function doASR() {
  if(!asrB64.value) { ElMessage.warning('请选择音频'); return }
  al.value=true
  try {
    const fd=new FormData(); fd.append('audio_base64',asrB64.value); fd.append('language',asrLang.value); fd.append('model_name',asrModel.value)
    const r=await axios.post(`${API}/asr`, fd)
    asrResult.value=r.data.result?.text||'未识别到文字'
    ElMessage.success('识别完成')
  } catch(e:any) { ElMessage.error('识别失败: '+e.message) }
  finally { al.value=false }
}

async function doTTS() {
  if(!ttsText.value) { ElMessage.warning('请输入文字'); return }
  tl.value=true
  try {
    const fd=new FormData(); fd.append('text',ttsText.value)
    const r=await axios.post(`${API}/tts`, fd)
    ttsAudio.value=r.data.audio_base64||''
    if(ttsAudio.value) ElMessage.success('合成完成')
    else ElMessage.error(r.data.error||'合成失败')
  } catch(e:any) { ElMessage.error('合成失败: '+e.message) }
  finally { tl.value=false }
}

function handleClsUpload(file:any) {
  const reader=new FileReader()
  reader.onload=(e)=>{ clsB64.value=(e.target?.result as string).split(',')[1] }
  reader.readAsDataURL(file.raw)
}

async function doClassify() {
  if(!clsB64.value) { ElMessage.warning('请选择音频'); return }
  cl.value=true
  try {
    const fd=new FormData(); fd.append('audio_base64',clsB64.value); fd.append('model_name',clsModel.value)
    const r=await axios.post(`${API}/classify`, fd)
    clsResult.value=r.data.predictions||[]
  } catch(e:any) { ElMessage.error('分类失败: '+e.message) }
  finally { cl.value=false }
}

function handleAnaUpload(file:any) {
  const reader=new FileReader()
  reader.onload=(e)=>{ anaB64.value=(e.target?.result as string).split(',')[1] }
  reader.readAsDataURL(file.raw)
}

async function doAnalyze() {
  if(!anaB64.value) { ElMessage.warning('请选择音频'); return }
  nll.value=true
  try {
    const fd=new FormData(); fd.append('audio_base64',anaB64.value)
    const r=await axios.post(`${API}/analyze`, fd)
    anaResult.value=r.data
  } catch(e:any) { ElMessage.error('分析失败: '+e.message) }
  finally { nll.value=false }
}
</script>

<style scoped>
.pg{padding:24px}.hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.hd h2{margin:0;font-size:20px}.hd-actions{display:flex;gap:8px}.st{margin-bottom:20px}
</style>
