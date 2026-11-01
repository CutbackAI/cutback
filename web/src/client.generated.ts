// Generated from openapi.json. Regenerate; do not hand-edit.
import type {components} from './api.generated';
type Request = (path:string,options?:RequestInit)=>Promise<any>;
export function createSDK(request:Request) {return {
  live_v1_health_live_get: (args:{}={}) => {
    const query=new URLSearchParams();
    return request(`/health/live`+(query.size?"?"+query.toString():""),{method:"GET"});},
  ready_v1_health_ready_get: (args:{}={}) => {
    const query=new URLSearchParams();
    return request(`/health/ready`+(query.size?"?"+query.toString():""),{method:"GET"});},
  project_create_v1_projects_post: (args:{request_id?:string | null;body:components["schemas"]["ProjectInput"]}) => {
    const query=new URLSearchParams();
    if(args.request_id!==undefined && args.request_id!==null) query.set("request_id",String(args.request_id));
    return request(`/projects`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  projects_v1_projects_get: (args:{limit?:number;offset?:number}={}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects`+(query.size?"?"+query.toString():""),{method:"GET"});},
  project_metadata_v1_projects__project_id__get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  delete_project_v1_projects__project_id__delete: (args:{project_id:string;confirm_slug:string}) => {
    const query=new URLSearchParams();
    if(args.confirm_slug!==undefined && args.confirm_slug!==null) query.set("confirm_slug",String(args.confirm_slug));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  key_create_v1_projects__project_id__keys_post: (args:{project_id:string;body:components["schemas"]["KeyInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/keys`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  key_list_v1_projects__project_id__keys_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/keys`+(query.size?"?"+query.toString():""),{method:"GET"});},
  key_revoke_v1_projects__project_id__keys__key_id__delete: (args:{project_id:string;key_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/keys/${encodeURIComponent(String(args.key_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  ingest_v1_ingest_events_post: (args:{body:components["schemas"]["EventBatch"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/events`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  usage_v1_projects__project_id__usage_get: (args:{project_id:string;start?:string | null;end?:string | null;group_by?:"model" | "provider" | "workflow" | "environment" | "day" | "call_site" | "traffic";traffic_kind?:"application" | "evaluation" | "judge" | "optimization" | "simulation" | "unknown" | null;call_site?:string | null}) => {
    const query=new URLSearchParams();
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    if(args.group_by!==undefined && args.group_by!==null) query.set("group_by",String(args.group_by));
    if(args.traffic_kind!==undefined && args.traffic_kind!==null) query.set("traffic_kind",String(args.traffic_kind));
    if(args.call_site!==undefined && args.call_site!==null) query.set("call_site",String(args.call_site));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/usage`+(query.size?"?"+query.toString():""),{method:"GET"});},
  events_v1_projects__project_id__events_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/events`+(query.size?"?"+query.toString():""),{method:"GET"});},
  audit_v1_projects__project_id__audit_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/audit`+(query.size?"?"+query.toString():""),{method:"GET"});},
  health_v1_ingest_health_post: (args:{body:components["schemas"]["HealthBatch"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/health`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  project_health_v1_projects__project_id__health_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/health`+(query.size?"?"+query.toString():""),{method:"GET"});},
  upload_scan_v1_projects__project_id__scans_post: (args:{project_id:string;request_id?:string | null;body:components["schemas"]["Inventory"]}) => {
    const query=new URLSearchParams();
    if(args.request_id!==undefined && args.request_id!==null) query.set("request_id",String(args.request_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scans`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  inventory_v1_projects__project_id__inventory_get: (args:{project_id:string;start?:string | null;end?:string | null}) => {
    const query=new URLSearchParams();
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/inventory`+(query.size?"?"+query.toString():""),{method:"GET"});},
  workflows_v1_projects__project_id__workflows_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/workflows`+(query.size?"?"+query.toString():""),{method:"GET"});},
  daily_v1_projects__project_id__daily_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/daily`+(query.size?"?"+query.toString():""),{method:"GET"});},
  override_v1_projects__project_id__prices_overrides_post: (args:{project_id:string;body:components["schemas"]["OverrideInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prices/overrides`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  prices_v1_projects__project_id__prices_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prices`+(query.size?"?"+query.toString():""),{method:"GET"});},
  opportunities_v1_projects__project_id__opportunities_get: (args:{project_id:string;start?:string | null;end?:string | null}) => {
    const query=new URLSearchParams();
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/opportunities`+(query.size?"?"+query.toString():""),{method:"GET"});},
  reconcile_lineage_v1_projects__project_id__datasets__dataset_id__lineage_reconcile_post: (args:{project_id:string;dataset_id:string;body:components["schemas"]["LineageReconciliation"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/datasets/${encodeURIComponent(String(args.dataset_id))}/lineage/reconcile`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  capture_policy_for_sdk_v1_ingest_capture_policies__policy_id__get: (args:{policy_id:string}) => {
    const query=new URLSearchParams();
    return request(`/ingest/capture-policies/${encodeURIComponent(String(args.policy_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  capture_sample_v1_ingest_samples_post: (args:{body:components["schemas"]["Sample"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/samples`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  plans_v1_projects__project_id__experiments_plans_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/plans`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_plan_v1_projects__project_id__experiments_plans_post: (args:{project_id:string;body:components["schemas"]["PlanInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/plans`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  create_policy_v1_projects__project_id__capture_policies_post: (args:{project_id:string;request_id?:string | null;body:components["schemas"]["CapturePolicy"]}) => {
    const query=new URLSearchParams();
    if(args.request_id!==undefined && args.request_id!==null) query.set("request_id",String(args.request_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/capture-policies`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  policies_v1_projects__project_id__capture_policies_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/capture-policies`+(query.size?"?"+query.toString():""),{method:"GET"});},
  revoke_policy_v1_projects__project_id__capture_policies__policy_id__delete: (args:{project_id:string;policy_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/capture-policies/${encodeURIComponent(String(args.policy_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  import_dataset_v1_projects__project_id__datasets_post: (args:{project_id:string;request_id?:string | null;body:components["schemas"]["DatasetImport"]}) => {
    const query=new URLSearchParams();
    if(args.request_id!==undefined && args.request_id!==null) query.set("request_id",String(args.request_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/datasets`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  datasets_v1_projects__project_id__datasets_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/datasets`+(query.size?"?"+query.toString():""),{method:"GET"});},
  dataset_detail_v1_projects__project_id__datasets__dataset_id__get: (args:{project_id:string;dataset_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/datasets/${encodeURIComponent(String(args.dataset_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_draft_v1_projects__project_id__experiments_draft_post: (args:{project_id:string;body:components["schemas"]["DraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  retrieval_draft_v1_projects__project_id__retrieval_draft_post: (args:{project_id:string;body:components["schemas"]["RetrievalDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/retrieval/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  retrieval_confirmation_v1_projects__project_id__retrieval_confirmation_post: (args:{project_id:string;body:components["schemas"]["RetrievalConfirmationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/retrieval/confirmation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  retrieval_proposal_v1_projects__project_id__retrieval__run_id__proposal_post: (args:{project_id:string;run_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/retrieval/${encodeURIComponent(String(args.run_id))}/proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  read_plan_content_v1_projects__project_id__experiments_plans__plan_id__content_post: (args:{project_id:string;plan_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/plans/${encodeURIComponent(String(args.plan_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  plan_execution_v1_projects__project_id__experiments_plans__plan_id__execution_get: (args:{project_id:string;plan_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/plans/${encodeURIComponent(String(args.plan_id))}/execution`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_plan_v1_projects__project_id__experiments_plans__plan_id__approve_post: (args:{project_id:string;plan_id:string;body:components["schemas"]["ApproveInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/plans/${encodeURIComponent(String(args.plan_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  queue_run_v1_projects__project_id__experiments_post: (args:{project_id:string;body:components["schemas"]["QueueInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  runs_v1_projects__project_id__experiments_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments`+(query.size?"?"+query.toString():""),{method:"GET"});},
  run_report_v1_projects__project_id__experiments__run_id__get: (args:{project_id:string;run_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  cancel_v1_projects__project_id__experiments__run_id__cancel_post: (args:{project_id:string;run_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST"});},
  recheck_blocked_v1_projects__project_id__experiments__run_id__recheck_post: (args:{project_id:string;run_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/recheck`+(query.size?"?"+query.toString():""),{method:"POST"});},
  recommendations_v1_projects__project_id__recommendations_get: (args:{project_id:string;run_id?:string | null;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.run_id!==undefined && args.run_id!==null) query.set("run_id",String(args.run_id));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/recommendations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  review_v1_projects__project_id__recommendations__recommendation_id__review_post: (args:{project_id:string;recommendation_id:string;body:components["schemas"]["ReviewInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/recommendations/${encodeURIComponent(String(args.recommendation_id))}/review`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  prepare_playground_v1_projects__project_id__prompts_playground_post: (args:{project_id:string;body:components["schemas"]["PromptPlayground"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/playground`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  draft_v1_projects__project_id__compression_draft_post: (args:{project_id:string;body:components["schemas"]["CompressionDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/compression/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  confirm_v1_projects__project_id__compression_confirmation_post: (args:{project_id:string;body:components["schemas"]["CompressionConfirmationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/compression/confirmation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  proposal_v1_projects__project_id__compression__run_id__proposal_post: (args:{project_id:string;run_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/compression/${encodeURIComponent(String(args.run_id))}/proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  draft_v1_projects__project_id__model_context_draft_post: (args:{project_id:string;body:components["schemas"]["JointContextDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/model-context/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  confirm_v1_projects__project_id__model_context_confirmation_post: (args:{project_id:string;body:components["schemas"]["JointContextConfirmationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/model-context/confirmation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  proposal_v1_projects__project_id__model_context__run_id__proposal_post: (args:{project_id:string;run_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/model-context/${encodeURIComponent(String(args.run_id))}/proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  draft_v1_projects__project_id__model_retrieval_draft_post: (args:{project_id:string;body:components["schemas"]["JointRetrievalDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/model-retrieval/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  confirm_v1_projects__project_id__model_retrieval_confirmation_post: (args:{project_id:string;body:components["schemas"]["JointRetrievalConfirmationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/model-retrieval/confirmation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  proposal_v1_projects__project_id__model_retrieval__run_id__proposal_post: (args:{project_id:string;run_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/model-retrieval/${encodeURIComponent(String(args.run_id))}/proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  draft_v1_projects__project_id__criteria_drafts_post: (args:{project_id:string;body:components["schemas"]["CriteriaDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/drafts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  list_versions_v1_projects__project_id__criteria_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria`+(query.size?"?"+query.toString():""),{method:"GET"});},
  status_v1_projects__project_id__criteria__criteria_id__get: (args:{project_id:string;criteria_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_generation_v1_projects__project_id__criteria__criteria_id__approve_generation_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["CriteriaBudgetApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/approve-generation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  queue_v1_projects__project_id__criteria__criteria_id__runs_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["CriteriaQueueInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/runs`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__criteria__criteria_id__content_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  review_v1_projects__project_id__criteria__criteria_id__review_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["CriteriaReviewInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/review`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  source_evidence_v1_projects__project_id__criteria__criteria_id__recommendations__recommendation_id__source_evidence_post: (args:{project_id:string;criteria_id:string;recommendation_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/recommendations/${encodeURIComponent(String(args.recommendation_id))}/source-evidence`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  evidence_validity_v1_projects__project_id__criteria__criteria_id__evidence_validity_get: (args:{project_id:string;criteria_id:string;run_id:string}) => {
    const query=new URLSearchParams();
    if(args.run_id!==undefined && args.run_id!==null) query.set("run_id",String(args.run_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/evidence-validity`+(query.size?"?"+query.toString():""),{method:"GET"});},
  cancel_v1_projects__project_id__criteria__criteria_id__cancel_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["CriteriaCancelInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  evaluation_draft_v1_projects__project_id__criteria__criteria_id__evaluation_draft_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["CriteriaEvaluationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/evaluation-draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  confirmation_v1_projects__project_id__criteria__criteria_id__confirmation_post: (args:{project_id:string;criteria_id:string;body:components["schemas"]["CriteriaConfirmationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/criteria/${encodeURIComponent(String(args.criteria_id))}/confirmation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__scenarios__scenario_id__content_post: (args:{project_id:string;scenario_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  import_reviewed_v1_projects__project_id__scenarios__scenario_id__imports_post: (args:{project_id:string;scenario_id:string;body:components["schemas"]["ScenarioImportInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}/imports`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  draft_v1_projects__project_id__scenarios_drafts_post: (args:{project_id:string;body:components["schemas"]["ScenarioDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/drafts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  list_versions_v1_projects__project_id__scenarios_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios`+(query.size?"?"+query.toString():""),{method:"GET"});},
  status_v1_projects__project_id__scenarios__scenario_id__get: (args:{project_id:string;scenario_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_generation_v1_projects__project_id__scenarios__scenario_id__approve_generation_post: (args:{project_id:string;scenario_id:string;body:components["schemas"]["ScenarioBudgetApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}/approve-generation`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  queue_v1_projects__project_id__scenarios__scenario_id__runs_post: (args:{project_id:string;scenario_id:string;body:components["schemas"]["ScenarioQueueInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}/runs`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  review_v1_projects__project_id__scenarios__scenario_id__review_post: (args:{project_id:string;scenario_id:string;body:components["schemas"]["ScenarioReviewInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}/review`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__scenarios__scenario_id__cancel_post: (args:{project_id:string;scenario_id:string;body:components["schemas"]["ScenarioCancelInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scenarios/${encodeURIComponent(String(args.scenario_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  training_draft_v1_projects__project_id__routing_training_drafts_post: (args:{project_id:string;body:components["schemas"]["RoutingTrainingDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/training-drafts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  list_fits_v1_projects__project_id__routing_fits_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/fits`+(query.size?"?"+query.toString():""),{method:"GET"});},
  fit_v1_projects__project_id__routing_fits_post: (args:{project_id:string;body:components["schemas"]["RoutingFitInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/fits`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  training_runs_v1_projects__project_id__routing_training_runs_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/training-runs`+(query.size?"?"+query.toString():""),{method:"GET"});},
  content_v1_projects__project_id__routing_fits__fit_id__content_post: (args:{project_id:string;fit_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/fits/${encodeURIComponent(String(args.fit_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  validation_draft_v1_projects__project_id__routing_fits__fit_id__validation_drafts_post: (args:{project_id:string;fit_id:string;body:components["schemas"]["RoutingValidationDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/fits/${encodeURIComponent(String(args.fit_id))}/validation-drafts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  source_proposal_v1_projects__project_id__routing_fits__fit_id__source_proposal_post: (args:{project_id:string;fit_id:string;body:components["schemas"]["RoutingSourceProposalInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/fits/${encodeURIComponent(String(args.fit_id))}/source-proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  confirmation_draft_v1_projects__project_id__routing_fits__fit_id__confirmation_drafts_post: (args:{project_id:string;fit_id:string;body:components["schemas"]["RoutingConfirmationDraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/fits/${encodeURIComponent(String(args.fit_id))}/confirmation-drafts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  import_policy_v1_projects__project_id__source_policies_post: (args:{project_id:string;body:components["schemas"]["SourcePolicyImport"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policies`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  prepare_v1_projects__project_id__source_policy_programs_post: (args:{project_id:string;body:components["schemas"]["SourcePolicyProgramDraft"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  listing_v1_projects__project_id__source_policy_programs_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs`+(query.size?"?"+query.toString():""),{method:"GET"});},
  detail_v1_projects__project_id__source_policy_programs__program_id__get: (args:{project_id:string;program_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs/${encodeURIComponent(String(args.program_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_v1_projects__project_id__source_policy_programs__program_id__approve_post: (args:{project_id:string;program_id:string;body:components["schemas"]["SourcePolicyProgramApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs/${encodeURIComponent(String(args.program_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__source_policy_programs__program_id__cancel_post: (args:{project_id:string;program_id:string;body:components["schemas"]["SourcePolicyProgramCancel"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs/${encodeURIComponent(String(args.program_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__source_policy_programs__program_id__content_post: (args:{project_id:string;program_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs/${encodeURIComponent(String(args.program_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  proposal_v1_projects__project_id__source_policy_programs__program_id__source_proposal_post: (args:{project_id:string;program_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/source-policy-programs/${encodeURIComponent(String(args.program_id))}/source-proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  prepare_v1_projects__project_id__routing_programs_post: (args:{project_id:string;body:components["schemas"]["RoutingProgramDraft"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  listing_v1_projects__project_id__routing_programs_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs`+(query.size?"?"+query.toString():""),{method:"GET"});},
  detail_v1_projects__project_id__routing_programs__program_id__get: (args:{project_id:string;program_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs/${encodeURIComponent(String(args.program_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_v1_projects__project_id__routing_programs__program_id__approve_post: (args:{project_id:string;program_id:string;body:components["schemas"]["RoutingProgramApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs/${encodeURIComponent(String(args.program_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__routing_programs__program_id__cancel_post: (args:{project_id:string;program_id:string;body:components["schemas"]["RoutingProgramCancel"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs/${encodeURIComponent(String(args.program_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__routing_programs__program_id__content_post: (args:{project_id:string;program_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs/${encodeURIComponent(String(args.program_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  proposal_v1_projects__project_id__routing_programs__program_id__source_proposal_post: (args:{project_id:string;program_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/routing/programs/${encodeURIComponent(String(args.program_id))}/source-proposal`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  queue_v1_projects__project_id__native_request_profiles_analyses_post: (args:{project_id:string;body:components["schemas"]["NativeProfileAnalysisInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/native-request-profiles/analyses`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  analyses_v1_projects__project_id__native_request_profiles_analyses_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/native-request-profiles/analyses`+(query.size?"?"+query.toString():""),{method:"GET"});},
  analysis_v1_projects__project_id__native_request_profiles_analyses__analysis_id__get: (args:{project_id:string;analysis_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/native-request-profiles/analyses/${encodeURIComponent(String(args.analysis_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  cancel_v1_projects__project_id__native_request_profiles_analyses__analysis_id__cancel_post: (args:{project_id:string;analysis_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/native-request-profiles/analyses/${encodeURIComponent(String(args.analysis_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST"});},
  assess_v1_projects__project_id__native_request_profiles_analyses__analysis_id__cache_trial_post: (args:{project_id:string;analysis_id:string;body:components["schemas"]["CacheWorkloadInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/native-request-profiles/analyses/${encodeURIComponent(String(args.analysis_id))}/cache-trial`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  templates_v1_projects__project_id__campaign_templates_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaign-templates`+(query.size?"?"+query.toString():""),{method:"GET"});},
  prepare_v1_projects__project_id__campaigns_post: (args:{project_id:string;body:components["schemas"]["CampaignInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  listing_v1_projects__project_id__campaigns_get: (args:{project_id:string;environment_id?:string | null;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns`+(query.size?"?"+query.toString():""),{method:"GET"});},
  draft_v1_projects__project_id__campaigns_draft_post: (args:{project_id:string;body:components["schemas"]["CampaignDraft"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  detail_v1_projects__project_id__campaigns__campaign_id__get: (args:{project_id:string;campaign_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns/${encodeURIComponent(String(args.campaign_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  selected_content_v1_projects__project_id__campaigns__campaign_id__selected_content_post: (args:{project_id:string;campaign_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns/${encodeURIComponent(String(args.campaign_id))}/selected/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  candidate_content_v1_projects__project_id__campaigns__campaign_id__candidates__candidate_id__content_post: (args:{project_id:string;campaign_id:string;candidate_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns/${encodeURIComponent(String(args.campaign_id))}/candidates/${encodeURIComponent(String(args.candidate_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  approve_v1_projects__project_id__campaigns__campaign_id__approve_post: (args:{project_id:string;campaign_id:string;body:components["schemas"]["CampaignApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns/${encodeURIComponent(String(args.campaign_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__campaigns__campaign_id__cancel_post: (args:{project_id:string;campaign_id:string;body:components["schemas"]["CampaignCancel"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/campaigns/${encodeURIComponent(String(args.campaign_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  tool_sessions_v1_projects__project_id__experiments__run_id__tool_sessions_get: (args:{project_id:string;run_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/tool-sessions`+(query.size?"?"+query.toString():""),{method:"GET"});},
  tool_operations_v1_projects__project_id__experiments__run_id__tool_sessions__session_id__operations_get: (args:{project_id:string;run_id:string;session_id:string;after?:number;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/tool-sessions/${encodeURIComponent(String(args.session_id))}/operations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  queue_tool_containment_v1_projects__project_id__experiments__run_id__tool_sessions__session_id__containment_post: (args:{project_id:string;run_id:string;session_id:string;body:components["schemas"]["ToolContainmentRequest"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/tool-sessions/${encodeURIComponent(String(args.session_id))}/containment`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  calls_v1_projects__project_id__experiments__run_id__calls_get: (args:{project_id:string;run_id:string;after?:number;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/calls`+(query.size?"?"+query.toString():""),{method:"GET"});},
  episodes_v1_projects__project_id__experiments__run_id__episodes_get: (args:{project_id:string;run_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/episodes`+(query.size?"?"+query.toString():""),{method:"GET"});},
  prepare_v1_projects__project_id__provider_batches_post: (args:{project_id:string;body:components["schemas"]["BatchPrepare"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/provider-batches`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  batches_v1_projects__project_id__provider_batches_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/provider-batches`+(query.size?"?"+query.toString():""),{method:"GET"});},
  batch_v1_projects__project_id__provider_batches__batch_id__get: (args:{project_id:string;batch_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/provider-batches/${encodeURIComponent(String(args.batch_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_v1_projects__project_id__provider_batches__batch_id__approve_post: (args:{project_id:string;batch_id:string;body:components["schemas"]["BatchApprove"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/provider-batches/${encodeURIComponent(String(args.batch_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__provider_batches__batch_id__cancel_post: (args:{project_id:string;batch_id:string;body:components["schemas"]["BatchControl"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/provider-batches/${encodeURIComponent(String(args.batch_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  reconcile_v1_projects__project_id__provider_batches__batch_id__reconcile_post: (args:{project_id:string;batch_id:string;body:components["schemas"]["BatchReconcile"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/provider-batches/${encodeURIComponent(String(args.batch_id))}/reconcile`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  ingest_v1_ingest_task_outcomes_post: (args:{body:components["schemas"]["TaskOutcomeBatch"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/task-outcomes`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  record_v1_projects__project_id__task_outcomes_post: (args:{project_id:string;environment_id:string;body:components["schemas"]["TaskOutcomeBatch"]}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-outcomes`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  tasks_v1_projects__project_id__task_outcomes_get: (args:{project_id:string;environment_id:string;as_of?:string | null;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.as_of!==undefined && args.as_of!==null) query.set("as_of",String(args.as_of));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-outcomes`+(query.size?"?"+query.toString():""),{method:"GET"});},
  economics_v1_projects__project_id__task_economics_get: (args:{project_id:string;environment_id:string;start:string;end:string;workflow?:string | null;release?:string | null;expected_tasks?:number | null;service?:string | null;as_of?:string | null}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    if(args.workflow!==undefined && args.workflow!==null) query.set("workflow",String(args.workflow));
    if(args.release!==undefined && args.release!==null) query.set("release",String(args.release));
    if(args.expected_tasks!==undefined && args.expected_tasks!==null) query.set("expected_tasks",String(args.expected_tasks));
    if(args.service!==undefined && args.service!==null) query.set("service",String(args.service));
    if(args.as_of!==undefined && args.as_of!==null) query.set("as_of",String(args.as_of));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-economics`+(query.size?"?"+query.toString():""),{method:"GET"});},
  freeze_v1_projects__project_id__task_cost_snapshots_post: (args:{project_id:string;environment_id:string;body:components["schemas"]["TaskSnapshotInput"]}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-cost-snapshots`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  snapshots_v1_projects__project_id__task_cost_snapshots_get: (args:{project_id:string;environment_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-cost-snapshots`+(query.size?"?"+query.toString():""),{method:"GET"});},
  snapshot_v1_projects__project_id__task_cost_snapshots__snapshot_id__get: (args:{project_id:string;snapshot_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-cost-snapshots/${encodeURIComponent(String(args.snapshot_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  snapshot_summary_v1_projects__project_id__task_cost_snapshots__snapshot_id__summary_get: (args:{project_id:string;snapshot_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/task-cost-snapshots/${encodeURIComponent(String(args.snapshot_id))}/summary`+(query.size?"?"+query.toString():""),{method:"GET"});},
  effort_v1_projects__project_id__search_effort_post: (args:{project_id:string;body:components["schemas"]["SearchEffortInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/search-effort`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  observe_v1_projects__project_id__improvement_observations_post: (args:{project_id:string;body:components["schemas"]["ImprovementInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/improvement-observations`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  observations_v1_projects__project_id__improvement_observations_get: (args:{project_id:string;environment_id?:string | null;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/improvement-observations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  draft_v1_projects__project_id__outcome_watches_draft_post: (args:{project_id:string;environment_id:string;body:components["schemas"]["OutcomeWatchIntent"]}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  propose_v1_projects__project_id__outcome_watches_post: (args:{project_id:string;environment_id:string;body:components["schemas"]["OutcomeWatchSpec-Input"]}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  watches_v1_projects__project_id__outcome_watches_get: (args:{project_id:string;environment_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches`+(query.size?"?"+query.toString():""),{method:"GET"});},
  read_v1_projects__project_id__outcome_watches__watch_id__get: (args:{project_id:string;watch_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches/${encodeURIComponent(String(args.watch_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  delete_v1_projects__project_id__outcome_watches__watch_id__delete: (args:{project_id:string;watch_id:string;confirm_watch_id:string}) => {
    const query=new URLSearchParams();
    if(args.confirm_watch_id!==undefined && args.confirm_watch_id!==null) query.set("confirm_watch_id",String(args.confirm_watch_id));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches/${encodeURIComponent(String(args.watch_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  approve_v1_projects__project_id__outcome_watches__watch_id__approve_post: (args:{project_id:string;watch_id:string;body:components["schemas"]["WatchApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches/${encodeURIComponent(String(args.watch_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  transition_v1_projects__project_id__outcome_watches__watch_id__state_post: (args:{project_id:string;watch_id:string;body:components["schemas"]["WatchTransition"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches/${encodeURIComponent(String(args.watch_id))}/state`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  windows_v1_projects__project_id__outcome_watches__watch_id__windows_get: (args:{project_id:string;watch_id:string;after?:number | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/outcome-watches/${encodeURIComponent(String(args.watch_id))}/windows`+(query.size?"?"+query.toString():""),{method:"GET"});},
  preview_v1_projects__project_id__imports_preview_post: (args:{project_id:string;body:components["schemas"]["ImportPreviewInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/imports/preview`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  previews_v1_projects__project_id__imports_get: (args:{project_id:string;before?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/imports`+(query.size?"?"+query.toString():""),{method:"GET"});},
  content_v1_projects__project_id__imports__preview_id__content_post: (args:{project_id:string;preview_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/imports/${encodeURIComponent(String(args.preview_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  metadata_v1_projects__project_id__imports__preview_id__get: (args:{project_id:string;preview_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/imports/${encodeURIComponent(String(args.preview_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  commit_v1_projects__project_id__imports__preview_id__commit_post: (args:{project_id:string;preview_id:string;body:components["schemas"]["ImportCommitInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/imports/${encodeURIComponent(String(args.preview_id))}/commit`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  principals_v1_projects__project_id__reviews_principals_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/principals`+(query.size?"?"+query.toString():""),{method:"GET"});},
  close_queue_v1_projects__project_id__reviews__queue_id__close_post: (args:{project_id:string;queue_id:string;body:components["schemas"]["ReviewClose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/close`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  source_items_v1_projects__project_id__review_sources_get: (args:{project_id:string;dataset_id?:string | null;trace_content_id?:string | null;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.dataset_id!==undefined && args.dataset_id!==null) query.set("dataset_id",String(args.dataset_id));
    if(args.trace_content_id!==undefined && args.trace_content_id!==null) query.set("trace_content_id",String(args.trace_content_id));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/review-sources`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_v1_projects__project_id__reviews_post: (args:{project_id:string;body:components["schemas"]["ReviewQueueInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  queues_v1_projects__project_id__reviews_get: (args:{project_id:string;before?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews`+(query.size?"?"+query.toString():""),{method:"GET"});},
  items_v1_projects__project_id__reviews__queue_id__items_get: (args:{project_id:string;queue_id:string;before?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/items`+(query.size?"?"+query.toString():""),{method:"GET"});},
  claim_v1_projects__project_id__reviews__queue_id__claim_post: (args:{project_id:string;queue_id:string;body:components["schemas"]["ReviewClaim"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/claim`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__reviews__queue_id__items__item_id__content_post: (args:{project_id:string;queue_id:string;item_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/items/${encodeURIComponent(String(args.item_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  release_v1_projects__project_id__reviews__queue_id__items__item_id__release_post: (args:{project_id:string;queue_id:string;item_id:string;body:components["schemas"]["ReviewRelease"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/items/${encodeURIComponent(String(args.item_id))}/release`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  vote_v1_projects__project_id__reviews__queue_id__items__item_id__votes_post: (args:{project_id:string;queue_id:string;item_id:string;body:components["schemas"]["ReviewSubmission"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/items/${encodeURIComponent(String(args.item_id))}/votes`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  adjudication_content_v1_projects__project_id__reviews__queue_id__items__item_id__adjudication_content_post: (args:{project_id:string;queue_id:string;item_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/items/${encodeURIComponent(String(args.item_id))}/adjudication-content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  adjudicate_v1_projects__project_id__reviews__queue_id__items__item_id__adjudicate_post: (args:{project_id:string;queue_id:string;item_id:string;body:components["schemas"]["ReviewAdjudication"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/items/${encodeURIComponent(String(args.item_id))}/adjudicate`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  promote_v1_projects__project_id__reviews__queue_id__promote_post: (args:{project_id:string;queue_id:string;body:components["schemas"]["ReviewPromotion"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/promote`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  calibration_v1_projects__project_id__reviews__queue_id__calibration_post: (args:{project_id:string;queue_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/reviews/${encodeURIComponent(String(args.queue_id))}/calibration`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  ingest_prompt_observations_v1_ingest_prompt_observations_post: (args:{body:components["schemas"]["PromptObservationBatch"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/prompt-observations`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  impact_v1_projects__project_id__prompts__prompt_id__impact_get: (args:{project_id:string;prompt_id:string;days?:number;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.days!==undefined && args.days!==null) query.set("days",String(args.days));
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/impact`+(query.size?"?"+query.toString():""),{method:"GET"});},
  prompts_v1_projects__project_id__prompts_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_v1_projects__project_id__prompts_post: (args:{project_id:string;body:components["schemas"]["PromptCreate"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  versions_v1_projects__project_id__prompts__prompt_id__versions_get: (args:{project_id:string;prompt_id:string;before?:number | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/versions`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_version_v1_projects__project_id__prompts__prompt_id__versions_post: (args:{project_id:string;prompt_id:string;body:components["schemas"]["PromptVersionInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/versions`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__prompts__prompt_id__versions__version_id__content_post: (args:{project_id:string;prompt_id:string;version_id:string;body:components["schemas"]["PromptContext"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/versions/${encodeURIComponent(String(args.version_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  preview_v1_projects__project_id__prompts__prompt_id__versions__version_id__preview_post: (args:{project_id:string;prompt_id:string;version_id:string;body:components["schemas"]["PromptRender"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/versions/${encodeURIComponent(String(args.version_id))}/preview`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  resolve_v1_projects__project_id__prompts_resolve__key__post: (args:{project_id:string;key:string;body:components["schemas"]["PromptResolve"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/resolve/${encodeURIComponent(String(args.key))}`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  grants_v1_projects__project_id__prompt_read_grants_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompt-read-grants`+(query.size?"?"+query.toString():""),{method:"GET"});},
  grant_v1_projects__project_id__prompts__prompt_id__read_grants_post: (args:{project_id:string;prompt_id:string;body:components["schemas"]["PromptGrantInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/read-grants`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  revoke_v1_projects__project_id__prompt_read_grants__grant_id__delete: (args:{project_id:string;grant_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompt-read-grants/${encodeURIComponent(String(args.grant_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  aliases_v1_projects__project_id__prompts__prompt_id__aliases_get: (args:{project_id:string;prompt_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/aliases`+(query.size?"?"+query.toString():""),{method:"GET"});},
  promote_v1_projects__project_id__prompts__prompt_id__aliases_post: (args:{project_id:string;prompt_id:string;body:components["schemas"]["PromptPromote"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/aliases`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  history_v1_projects__project_id__prompt_aliases__alias_id__history_get: (args:{project_id:string;alias_id:string;before_revision?:number | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before_revision!==undefined && args.before_revision!==null) query.set("before_revision",String(args.before_revision));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompt-aliases/${encodeURIComponent(String(args.alias_id))}/history`+(query.size?"?"+query.toString():""),{method:"GET"});},
  rollback_v1_projects__project_id__prompt_aliases__alias_id__rollback_post: (args:{project_id:string;alias_id:string;body:components["schemas"]["PromptRollback"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompt-aliases/${encodeURIComponent(String(args.alias_id))}/rollback`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  diff_v1_projects__project_id__prompts__prompt_id__diff_post: (args:{project_id:string;prompt_id:string;body:components["schemas"]["PromptDiff"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prompts/${encodeURIComponent(String(args.prompt_id))}/diff`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  create_online_v1_projects__project_id__online_rules_post: (args:{project_id:string;body:components["schemas"]["OnlineRuleInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/online-rules`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  list_online_v1_projects__project_id__online_rules_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/online-rules`+(query.size?"?"+query.toString():""),{method:"GET"});},
  switch_online_v1_projects__project_id__online_rules__rule_id__switch_post: (args:{project_id:string;rule_id:string;body:components["schemas"]["RuleSwitch"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/online-rules/${encodeURIComponent(String(args.rule_id))}/switch`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  evaluations_v1_projects__project_id__online_rules__rule_id__evaluations_get: (args:{project_id:string;rule_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/online-rules/${encodeURIComponent(String(args.rule_id))}/evaluations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_alert_v1_projects__project_id__alert_rules_post: (args:{project_id:string;body:components["schemas"]["AlertRuleInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/alert-rules`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  list_alerts_v1_projects__project_id__alert_rules_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/alert-rules`+(query.size?"?"+query.toString():""),{method:"GET"});},
  switch_alert_v1_projects__project_id__alert_rules__rule_id__switch_post: (args:{project_id:string;rule_id:string;body:components["schemas"]["RuleSwitch"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/alert-rules/${encodeURIComponent(String(args.rule_id))}/switch`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  alert_windows_v1_projects__project_id__alert_rules__rule_id__windows_get: (args:{project_id:string;rule_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/alert-rules/${encodeURIComponent(String(args.rule_id))}/windows`+(query.size?"?"+query.toString():""),{method:"GET"});},
  incidents_v1_projects__project_id__incidents_get: (args:{project_id:string;after?:string | null;state?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.state!==undefined && args.state!==null) query.set("state",String(args.state));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/incidents`+(query.size?"?"+query.toString():""),{method:"GET"});},
  incident_detail_v1_projects__project_id__incidents__incident_id__get: (args:{project_id:string;incident_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/incidents/${encodeURIComponent(String(args.incident_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  transition_v1_projects__project_id__incidents__incident_id__transition_post: (args:{project_id:string;incident_id:string;body:components["schemas"]["IncidentTransition"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/incidents/${encodeURIComponent(String(args.incident_id))}/transition`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  incident_evaluations_v1_projects__project_id__incidents__incident_id__evaluations_get: (args:{project_id:string;incident_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/incidents/${encodeURIComponent(String(args.incident_id))}/evaluations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  save_view_v1_projects__project_id__saved_views_post: (args:{project_id:string;body:components["schemas"]["SavedViewInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/saved-views`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  saved_views_v1_projects__project_id__saved_views_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/saved-views`+(query.size?"?"+query.toString():""),{method:"GET"});},
  delete_view_v1_projects__project_id__saved_views__view_id__delete: (args:{project_id:string;view_id:string;expected_revision:number}) => {
    const query=new URLSearchParams();
    if(args.expected_revision!==undefined && args.expected_revision!==null) query.set("expected_revision",String(args.expected_revision));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/saved-views/${encodeURIComponent(String(args.view_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  allowed_v1_projects__project_id__notification_destinations_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/notification-destinations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  configure_v1_projects__project_id__notifications_post: (args:{project_id:string;body:components["schemas"]["NotificationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/notifications`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  configs_v1_projects__project_id__notifications_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/notifications`+(query.size?"?"+query.toString():""),{method:"GET"});},
  deliveries_v1_projects__project_id__notification_deliveries_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/notification-deliveries`+(query.size?"?"+query.toString():""),{method:"GET"});},
  compare_v1_projects__project_id__quality_rules__rule_id__compare_post: (args:{project_id:string;rule_id:string;body:components["schemas"]["QualityComparison"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/quality-rules/${encodeURIComponent(String(args.rule_id))}/compare`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  create_rule_v1_projects__project_id__quality_rules_post: (args:{project_id:string;body:components["schemas"]["QualityRuleInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/quality-rules`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  rules_v1_projects__project_id__quality_rules_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/quality-rules`+(query.size?"?"+query.toString():""),{method:"GET"});},
  switch_v1_projects__project_id__quality_rules__rule_id__switch_post: (args:{project_id:string;rule_id:string;body:components["schemas"]["RuleSwitch"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/quality-rules/${encodeURIComponent(String(args.rule_id))}/switch`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  evaluations_v1_projects__project_id__quality_rules__rule_id__evaluations_get: (args:{project_id:string;rule_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/quality-rules/${encodeURIComponent(String(args.rule_id))}/evaluations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  read_output_v1_projects__project_id__quality_evaluations__evaluation_id__output_post: (args:{project_id:string;evaluation_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/quality-evaluations/${encodeURIComponent(String(args.evaluation_id))}/output`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  create_v1_projects__project_id__gateway_policies_post: (args:{project_id:string;body:components["schemas"]["GatewayPolicy-Input"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/policies`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  policies_v1_projects__project_id__gateway_policies_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/policies`+(query.size?"?"+query.toString():""),{method:"GET"});},
  detail_v1_projects__project_id__gateway_policies__policy_id__get: (args:{project_id:string;policy_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/policies/${encodeURIComponent(String(args.policy_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_v1_projects__project_id__gateway_policies__policy_id__approve_post: (args:{project_id:string;policy_id:string;body:components["schemas"]["GatewayApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/policies/${encodeURIComponent(String(args.policy_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  disable_v1_projects__project_id__gateway_policies__policy_id__disable_post: (args:{project_id:string;policy_id:string;body:components["schemas"]["GatewayExactPolicy"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/policies/${encodeURIComponent(String(args.policy_id))}/disable`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  resume_v1_projects__project_id__gateway_policies__policy_id__resume_post: (args:{project_id:string;policy_id:string;body:components["schemas"]["GatewayApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/policies/${encodeURIComponent(String(args.policy_id))}/resume`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  issue_v1_projects__project_id__gateway_keys_post: (args:{project_id:string;body:components["schemas"]["GatewayKeyInput-Input"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/keys`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  keys_v1_projects__project_id__gateway_keys_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/keys`+(query.size?"?"+query.toString():""),{method:"GET"});},
  revoke_v1_projects__project_id__gateway_keys__key_id__delete: (args:{project_id:string;key_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/keys/${encodeURIComponent(String(args.key_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  calls_v1_projects__project_id__gateway_calls_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/gateway/calls`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_v1_projects__project_id__runtime_guardrails_post: (args:{project_id:string;body:components["schemas"]["GuardrailRegistration"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  listing_v1_projects__project_id__runtime_guardrails_get: (args:{project_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails`+(query.size?"?"+query.toString():""),{method:"GET"});},
  detail_v1_projects__project_id__runtime_guardrails__policy_id__get: (args:{project_id:string;policy_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails/${encodeURIComponent(String(args.policy_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_v1_projects__project_id__runtime_guardrails__policy_id__approve_post: (args:{project_id:string;policy_id:string;body:components["schemas"]["GuardrailActivation"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails/${encodeURIComponent(String(args.policy_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  turn_off_v1_projects__project_id__runtime_guardrails__policy_id__disable_post: (args:{project_id:string;policy_id:string;body:components["schemas"]["GuardrailDisable"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails/${encodeURIComponent(String(args.policy_id))}/disable`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__runtime_guardrails__policy_id__content_post: (args:{project_id:string;policy_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails/${encodeURIComponent(String(args.policy_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  operations_v1_projects__project_id__runtime_guardrails__policy_id__operations_get: (args:{project_id:string;policy_id:string;after?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.after!==undefined && args.after!==null) query.set("after",String(args.after));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails/${encodeURIComponent(String(args.policy_id))}/operations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  operation_v1_projects__project_id__runtime_guardrails__policy_id__operations__operation_id___stage__get: (args:{project_id:string;policy_id:string;operation_id:string;stage:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/runtime-guardrails/${encodeURIComponent(String(args.policy_id))}/operations/${encodeURIComponent(String(args.operation_id))}/${encodeURIComponent(String(args.stage))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  export_v1_projects__project_id__export_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/export`+(query.size?"?"+query.toString():""),{method:"GET"});},
  auth_configuration_v1_auth_config_get: (args:{}={}) => {
    const query=new URLSearchParams();
    return request(`/auth/config`+(query.size?"?"+query.toString():""),{method:"GET"});},
  login_start_v1_auth_oidc_start_post: (args:{body:components["schemas"]["LoginStart"]}) => {
    const query=new URLSearchParams();
    return request(`/auth/oidc/start`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  login_exchange_v1_auth_oidc_exchange_post: (args:{body:components["schemas"]["LoginExchange"]}) => {
    const query=new URLSearchParams();
    return request(`/auth/oidc/exchange`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  me_v1_me_get: (args:{}={}) => {
    const query=new URLSearchParams();
    return request(`/me`+(query.size?"?"+query.toString():""),{method:"GET"});},
  organizations_v1_organizations_get: (args:{}={}) => {
    const query=new URLSearchParams();
    return request(`/organizations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_organization_v1_organizations_post: (args:{body:components["schemas"]["OrganizationInput"]}) => {
    const query=new URLSearchParams();
    return request(`/organizations`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  members_v1_organizations__organization_id__members_get: (args:{organization_id:string}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/members`+(query.size?"?"+query.toString():""),{method:"GET"});},
  add_member_v1_organizations__organization_id__members_post: (args:{organization_id:string;body:components["schemas"]["MemberInput"]}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/members`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  remove_member_v1_organizations__organization_id__members__member_id__delete: (args:{organization_id:string;member_id:string}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/members/${encodeURIComponent(String(args.member_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  grant_v1_organizations__organization_id__members__member_id__grants_post: (args:{organization_id:string;member_id:string;body:components["schemas"]["GrantInput"]}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/members/${encodeURIComponent(String(args.member_id))}/grants`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  revoke_grant_v1_organizations__organization_id__members__member_id__grants__project_id__delete: (args:{organization_id:string;member_id:string;project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/members/${encodeURIComponent(String(args.member_id))}/grants/${encodeURIComponent(String(args.project_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  settings_v1_projects__project_id__settings_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/settings`+(query.size?"?"+query.toString():""),{method:"GET"});},
  update_settings_v1_projects__project_id__settings_put: (args:{project_id:string;body:components["schemas"]["RetentionInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/settings`+(query.size?"?"+query.toString():""),{method:"PUT",body:JSON.stringify(args.body)});},
  schedules_v1_projects__project_id__schedules_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/schedules`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_schedule_v1_projects__project_id__schedules_post: (args:{project_id:string;body:components["schemas"]["ScheduleInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/schedules`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  approve_schedule_v1_projects__project_id__schedules__schedule_id__approve_post: (args:{project_id:string;schedule_id:string;body:components["schemas"]["Approval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/schedules/${encodeURIComponent(String(args.schedule_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  disable_schedule_v1_projects__project_id__schedules__schedule_id__delete: (args:{project_id:string;schedule_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/schedules/${encodeURIComponent(String(args.schedule_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  rollouts_v1_projects__project_id__rollouts_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/rollouts`+(query.size?"?"+query.toString():""),{method:"GET"});},
  create_rollout_v1_projects__project_id__rollouts_post: (args:{project_id:string;body:components["schemas"]["RolloutInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/rollouts`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  approve_rollout_v1_projects__project_id__rollouts__rollout_id__approve_post: (args:{project_id:string;rollout_id:string;body:components["schemas"]["Approval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/rollouts/${encodeURIComponent(String(args.rollout_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  transition_v1_projects__project_id__rollouts__rollout_id__transition_post: (args:{project_id:string;rollout_id:string;body:components["schemas"]["RolloutTransition"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/rollouts/${encodeURIComponent(String(args.rollout_id))}/transition`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  observations_v1_projects__project_id__rollouts__rollout_id__observations_post: (args:{project_id:string;rollout_id:string;body:components["schemas"]["RolloutObservation"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/rollouts/${encodeURIComponent(String(args.rollout_id))}/observations`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  billing_v1_organizations__organization_id__billing_get: (args:{organization_id:string}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/billing`+(query.size?"?"+query.toString():""),{method:"GET"});},
  caps_v1_organizations__organization_id__billing_caps_put: (args:{organization_id:string;body:components["schemas"]["BillingCaps"]}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/billing/caps`+(query.size?"?"+query.toString():""),{method:"PUT",body:JSON.stringify(args.body)});},
  checkout_v1_organizations__organization_id__billing_checkout_post: (args:{organization_id:string;body:components["schemas"]["CheckoutInput"]}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/billing/checkout`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  portal_v1_organizations__organization_id__billing_portal_post: (args:{organization_id:string}) => {
    const query=new URLSearchParams();
    return request(`/organizations/${encodeURIComponent(String(args.organization_id))}/billing/portal`+(query.size?"?"+query.toString():""),{method:"POST"});},
  webhook_v1_billing_webhook_post: (args:{}={}) => {
    const query=new URLSearchParams();
    return request(`/billing/webhook`+(query.size?"?"+query.toString():""),{method:"POST"});},
  prepare_key_v1_projects__project_id__scoped_keys_post: (args:{project_id:string;body:components["schemas"]["ScopedKeyInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scoped-keys`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  lookup_v1_ingest_cache_lookup_post: (args:{body:components["schemas"]["CacheKey"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/cache/lookup`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  put_v1_ingest_cache_put_post: (args:{body:components["schemas"]["CachePut"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/cache/put`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  entries_v1_projects__project_id__cache_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/cache`+(query.size?"?"+query.toString():""),{method:"GET"});},
  invalidate_v1_projects__project_id__cache__cache_id__delete: (args:{project_id:string;cache_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/cache/${encodeURIComponent(String(args.cache_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  create_v1_projects__project_id__semantic_cache_namespaces_post: (args:{project_id:string;body:components["schemas"]["NamespaceInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  namespaces_v1_projects__project_id__semantic_cache_namespaces_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces`+(query.size?"?"+query.toString():""),{method:"GET"});},
  entries_v1_projects__project_id__semantic_cache_namespaces__namespace_id__entries_get: (args:{project_id:string;namespace_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}/entries`+(query.size?"?"+query.toString():""),{method:"GET"});},
  entry_content_v1_projects__project_id__semantic_cache_namespaces__namespace_id__entries__entry_id__content_post: (args:{project_id:string;namespace_id:string;entry_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}/entries/${encodeURIComponent(String(args.entry_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  delete_namespace_v1_projects__project_id__semantic_cache_namespaces__namespace_id__delete: (args:{project_id:string;namespace_id:string;body:components["schemas"]["SemanticApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE",body:JSON.stringify(args.body)});},
  inspect_namespace_v1_projects__project_id__semantic_cache_namespaces__namespace_id__get: (args:{project_id:string;namespace_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  revoke_v1_projects__project_id__semantic_cache_namespaces__namespace_id__revoke_post: (args:{project_id:string;namespace_id:string;body:components["schemas"]["SemanticControl"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}/revoke`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  put_v1_ingest_semantic_cache_put_post: (args:{body:components["schemas"]["SemanticPut"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/semantic-cache/put`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  lookup_v1_ingest_semantic_cache_lookup_post: (args:{body:components["schemas"]["SemanticLookup"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/semantic-cache/lookup`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  prepare_v1_projects__project_id__semantic_cache_namespaces__namespace_id__qualifications_post: (args:{project_id:string;namespace_id:string;body:components["schemas"]["QualificationPrepare"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}/qualifications`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  qualifications_v1_projects__project_id__semantic_cache_qualifications_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/qualifications`+(query.size?"?"+query.toString():""),{method:"GET"});},
  inspect_qualification_v1_projects__project_id__semantic_cache_qualifications__qualification_id__get: (args:{project_id:string;qualification_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/qualifications/${encodeURIComponent(String(args.qualification_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  approve_v1_projects__project_id__semantic_cache_qualifications__qualification_id__approve_post: (args:{project_id:string;qualification_id:string;body:components["schemas"]["SemanticApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/qualifications/${encodeURIComponent(String(args.qualification_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__semantic_cache_qualifications__qualification_id__cancel_post: (args:{project_id:string;qualification_id:string;body:components["schemas"]["SemanticControl"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/qualifications/${encodeURIComponent(String(args.qualification_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  activation_v1_projects__project_id__semantic_cache_qualifications__qualification_id__activation_get: (args:{project_id:string;qualification_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/qualifications/${encodeURIComponent(String(args.qualification_id))}/activation`+(query.size?"?"+query.toString():""),{method:"GET"});},
  activate_v1_projects__project_id__semantic_cache_qualifications__qualification_id__activate_post: (args:{project_id:string;qualification_id:string;body:components["schemas"]["SemanticApproval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/qualifications/${encodeURIComponent(String(args.qualification_id))}/activate`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  prepare_v1_projects__project_id__semantic_cache_namespaces__namespace_id__dataset_uploads_post: (args:{project_id:string;namespace_id:string;body:components["schemas"]["SemanticUploadPrepare"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/namespaces/${encodeURIComponent(String(args.namespace_id))}/dataset-uploads`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  show_v1_projects__project_id__semantic_cache_dataset_uploads__upload_id__get: (args:{project_id:string;upload_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/dataset-uploads/${encodeURIComponent(String(args.upload_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  append_v1_projects__project_id__semantic_cache_dataset_uploads__upload_id__append_post: (args:{project_id:string;upload_id:string;body:components["schemas"]["SemanticUploadAppend"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/dataset-uploads/${encodeURIComponent(String(args.upload_id))}/append`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  finalize_v1_projects__project_id__semantic_cache_dataset_uploads__upload_id__finalize_post: (args:{project_id:string;upload_id:string;body:components["schemas"]["SemanticUploadSeal"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/dataset-uploads/${encodeURIComponent(String(args.upload_id))}/finalize`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__semantic_cache_dataset_uploads__upload_id__cancel_post: (args:{project_id:string;upload_id:string;body:components["schemas"]["SemanticUploadSeal"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/semantic-cache/dataset-uploads/${encodeURIComponent(String(args.upload_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  configuration_v1_projects__project_id__training_config_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/training/config`+(query.size?"?"+query.toString():""),{method:"GET"});},
  jobs_v1_projects__project_id__training_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/training`+(query.size?"?"+query.toString():""),{method:"GET"});},
  propose_v1_projects__project_id__training_post: (args:{project_id:string;body:components["schemas"]["TrainingInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/training`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  approve_v1_projects__project_id__training__training_id__approve_post: (args:{project_id:string;training_id:string;body:components["schemas"]["Approval"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/training/${encodeURIComponent(String(args.training_id))}/approve`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  cancel_v1_projects__project_id__training__training_id__cancel_post: (args:{project_id:string;training_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/training/${encodeURIComponent(String(args.training_id))}/cancel`+(query.size?"?"+query.toString():""),{method:"POST"});},
  artifact_v1_projects__project_id__training__training_id__artifacts__artifact_index__post: (args:{project_id:string;training_id:string;artifact_index:number;body:components["schemas"]["ArtifactAccess"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/training/${encodeURIComponent(String(args.training_id))}/artifacts/${encodeURIComponent(String(args.artifact_index))}`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  paired_v1_projects__project_id__experiments__run_id__cases_paired_post: (args:{project_id:string;run_id:string;limit?:number;offset?:number;failed_only?:boolean;model_key?:string | null;stratum?:string | null;criterion?:string | null;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    if(args.failed_only!==undefined && args.failed_only!==null) query.set("failed_only",String(args.failed_only));
    if(args.model_key!==undefined && args.model_key!==null) query.set("model_key",String(args.model_key));
    if(args.stratum!==undefined && args.stratum!==null) query.set("stratum",String(args.stratum));
    if(args.criterion!==undefined && args.criterion!==null) query.set("criterion",String(args.criterion));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/cases/paired`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  correct_v1_projects__project_id__experiments__run_id__cases_correct_post: (args:{project_id:string;run_id:string;body:components["schemas"]["CorrectionInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/cases/correct`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  content_v1_projects__project_id__experiments__run_id__cases_content_post: (args:{project_id:string;run_id:string;limit?:number;offset?:number;legacy_only?:boolean;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    if(args.legacy_only!==undefined && args.legacy_only!==null) query.set("legacy_only",String(args.legacy_only));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/${encodeURIComponent(String(args.run_id))}/cases/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  upload_v1_projects__project_id__recommendations__recommendation_id__patches_post: (args:{project_id:string;recommendation_id:string;body:components["schemas"]["PatchUpload"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/recommendations/${encodeURIComponent(String(args.recommendation_id))}/patches`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  list_changes_v1_projects__project_id__changes_get: (args:{project_id:string;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/changes`+(query.size?"?"+query.toString():""),{method:"GET"});},
  content_v1_projects__project_id__changes__change_id__content_post: (args:{project_id:string;change_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/changes/${encodeURIComponent(String(args.change_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  ingest_v1_ingest_otel_v1_traces_post: (args:{body:components["schemas"]["TraceExport"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/otel/v1/traces`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  environments_v1_projects__project_id__environments_get: (args:{project_id:string;before?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/environments`+(query.size?"?"+query.toString():""),{method:"GET"});},
  environment_create_v1_projects__project_id__environments_post: (args:{project_id:string;body:components["schemas"]["EnvironmentInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/environments`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  workflow_spans_v1_ingest_spans_post: (args:{body:components["schemas"]["SpanBatch"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/spans`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  external_evaluations_v1_ingest_evaluations_post: (args:{body:components["schemas"]["EvaluationBatch"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/evaluations`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  traces_v1_projects__project_id__traces_get: (args:{project_id:string;environment_id:string;session_record_id?:string | null;before?:string | null;errors_only?:boolean;workflow?:string | null;service?:string | null;release?:string | null;model?:string | null;start?:string | null;end?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.session_record_id!==undefined && args.session_record_id!==null) query.set("session_record_id",String(args.session_record_id));
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.errors_only!==undefined && args.errors_only!==null) query.set("errors_only",String(args.errors_only));
    if(args.workflow!==undefined && args.workflow!==null) query.set("workflow",String(args.workflow));
    if(args.service!==undefined && args.service!==null) query.set("service",String(args.service));
    if(args.release!==undefined && args.release!==null) query.set("release",String(args.release));
    if(args.model!==undefined && args.model!==null) query.set("model",String(args.model));
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/traces`+(query.size?"?"+query.toString():""),{method:"GET"});},
  detail_v1_projects__project_id__traces__trace_record_id__get: (args:{project_id:string;trace_record_id:string;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/traces/${encodeURIComponent(String(args.trace_record_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  compare_v1_projects__project_id__traces__trace_record_id__compare__other_trace_id__get: (args:{project_id:string;trace_record_id:string;other_trace_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/traces/${encodeURIComponent(String(args.trace_record_id))}/compare/${encodeURIComponent(String(args.other_trace_id))}`+(query.size?"?"+query.toString():""),{method:"GET"});},
  session_list_v1_projects__project_id__sessions_get: (args:{project_id:string;environment_id:string;before?:string | null;errors_only?:boolean;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.environment_id!==undefined && args.environment_id!==null) query.set("environment_id",String(args.environment_id));
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.errors_only!==undefined && args.errors_only!==null) query.set("errors_only",String(args.errors_only));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/sessions`+(query.size?"?"+query.toString():""),{method:"GET"});},
  session_evaluations_v1_projects__project_id__sessions__session_record_id__evaluations_get: (args:{project_id:string;session_record_id:string;before?:string | null;limit?:number}) => {
    const query=new URLSearchParams();
    if(args.before!==undefined && args.before!==null) query.set("before",String(args.before));
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/sessions/${encodeURIComponent(String(args.session_record_id))}/evaluations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  projected_traces_v1_ingest_workflow_otel_v1_traces_post: (args:{body:components["schemas"]["TraceExport"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/workflow-otel/v1/traces`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  capture_v1_ingest_trace_content_post: (args:{body:components["schemas"]["TraceContentInput"]}) => {
    const query=new URLSearchParams();
    return request(`/ingest/trace-content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  read_v1_projects__project_id__traces__trace_record_id__spans__span_id__content_post: (args:{project_id:string;trace_record_id:string;span_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/traces/${encodeURIComponent(String(args.trace_record_id))}/spans/${encodeURIComponent(String(args.span_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
};}
