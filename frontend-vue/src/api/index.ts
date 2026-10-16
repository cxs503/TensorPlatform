// ============================================================
// API barrel file — re-exports from domain modules
// ============================================================
// Domain modules:
//   auth.ts        — authApi, userApi, apiKeyApi
//   data.ts        — projectApi, datasetApi, dataSourceApi,
//                    dataGovernanceApi, dataCatalogApi, lineageApi, qualityApi,
//                    securityApi, dataMaskingApi, dataCollectionApi, dataSubscriptionApi
//   model.ts       — modelApi, trainingApi, modelTrainingApi, hyperoptApi,
//                    modelVersionApi, modelEvaluationApi, labelingApi,
//                    modelExplainApi, pipelineApi, featureStoreApi,
//                    distributedTrainingApi, realTrainingApi, onnxApi
//   deployment.ts  — deploymentApi
//   monitoring.ts  — dashboardApi, alertApi, loggingApi, notificationApi,
//                    alertManageApi, reportApi
//   platform.ts    — workOrderApi, remoteOpsApi, rbacApi, auditApi, kmsApi,
//                    backupApi, tenantApi, configApi, gpuApi, cacheApi,
//                    serviceMeshApi, gatewayApi, complianceApi,
//                    complianceManagementApi, governanceApi, streamingApi
//   mlops.ts       — mlflowTrackingApi, driftDetectionApi
//   marketplace.ts — marketplaceApi, algorithmStoreApi, notebookApi
// ============================================================

import http from './request'

// Auth
export { authApi, userApi, apiKeyApi } from './auth'

// Data
export { projectApi, datasetApi, dataSourceApi,
         dataGovernanceApi, dataCatalogApi, lineageApi, qualityApi,
         securityApi, dataMaskingApi, dataCollectionApi, dataSubscriptionApi } from './data'

// Model
export { modelApi, trainingApi, modelTrainingApi, hyperoptApi,
         modelVersionApi, modelEvaluationApi, labelingApi,
         modelExplainApi, pipelineApi, featureStoreApi,
         distributedTrainingApi, realTrainingApi, onnxApi } from './model'

// Deployment
export { deploymentApi } from './deployment'

// Monitoring
export { dashboardApi, alertApi, loggingApi, notificationApi,
         alertManageApi, reportApi } from './monitoring'

// Platform
export { workOrderApi, remoteOpsApi, rbacApi, auditApi, kmsApi,
         backupApi, tenantApi, configApi, gpuApi, cacheApi,
         serviceMeshApi, gatewayApi, complianceApi,
         complianceManagementApi, governanceApi, streamingApi } from './platform'

// MLOps
export { mlflowTrackingApi, driftDetectionApi } from './mlops'

// Marketplace
export { marketplaceApi, algorithmStoreApi, notebookApi } from './marketplace'

// ============================================================
// Unified api object (backward compatible)
// ============================================================
import { authApi, userApi, apiKeyApi } from './auth'
import { projectApi, datasetApi, dataSourceApi,
         dataGovernanceApi, dataCatalogApi, lineageApi, qualityApi,
         securityApi, dataMaskingApi, dataCollectionApi, dataSubscriptionApi } from './data'
import { modelApi, trainingApi, modelTrainingApi, hyperoptApi,
         modelVersionApi, modelEvaluationApi, labelingApi,
         modelExplainApi, pipelineApi, featureStoreApi,
         distributedTrainingApi, realTrainingApi, onnxApi } from './model'
import { deploymentApi } from './deployment'
import { dashboardApi, alertApi, loggingApi, notificationApi,
         alertManageApi, reportApi } from './monitoring'
import { workOrderApi, remoteOpsApi, rbacApi, auditApi, kmsApi,
         backupApi, tenantApi, configApi, gpuApi, cacheApi,
         serviceMeshApi, gatewayApi, complianceApi,
         complianceManagementApi, governanceApi, streamingApi } from './platform'
import { mlflowTrackingApi, driftDetectionApi } from './mlops'
import { marketplaceApi, algorithmStoreApi, notebookApi } from './marketplace'

export const api = {
  auth: authApi,
  user: userApi,
  project: projectApi,
  dataset: datasetApi,
  model: modelApi,
  training: trainingApi,
  deployment: deploymentApi,
  dashboard: dashboardApi,
  alert: alertApi,
  workOrder: workOrderApi,
  dataSource: dataSourceApi,
  dataGovernance: dataGovernanceApi,
  hyperopt: hyperoptApi,
  dataCatalog: dataCatalogApi,
  lineage: lineageApi,
  quality: qualityApi,
  security: securityApi,
  config: configApi,
  gpu: gpuApi,
  cache: cacheApi,
  tenant: tenantApi,
  report: reportApi,
  notification: notificationApi,
  audit: auditApi,
  apiKey: apiKeyApi,
  modelVersion: modelVersionApi,
  modelEvaluation: modelEvaluationApi,
  labeling: labelingApi,
  modelExplain: modelExplainApi,
  pipeline: pipelineApi,
  featureStore: featureStoreApi,
  distributedTraining: distributedTrainingApi,
  streaming: streamingApi,
  remoteOps: remoteOpsApi,
  logging: loggingApi,
  backup: backupApi,
  serviceMesh: serviceMeshApi,
  kms: kmsApi,
  compliance: complianceApi,
  governance: governanceApi,
  dataMasking: dataMaskingApi,
  dataCollection: dataCollectionApi,
  dataSubscription: dataSubscriptionApi,
  gateway: gatewayApi,
  complianceManagement: complianceManagementApi,
  notebook: notebookApi,
  modelTraining: modelTrainingApi,
  rbac: rbacApi,
  marketplace: marketplaceApi,
  algorithmStore: algorithmStoreApi,
  driftDetection: driftDetectionApi,
  realTraining: realTrainingApi,
  mlflowTracking: mlflowTrackingApi,
  onnx: onnxApi,
  // 直接方法
  get: (url: string, params?: any) => http.get(url, params),
  post: (url: string, data?: any) => http.post(url, data),
  put: (url: string, data?: any) => http.put(url, data),
  delete: (url: string) => http.delete(url)
}
