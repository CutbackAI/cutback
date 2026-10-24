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
  project_create_v1_projects_post: (args:{body:components["schemas"]["ProjectInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  projects_v1_projects_get: (args:{limit?:number;offset?:number}={}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects`+(query.size?"?"+query.toString():""),{method:"GET"});},
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
  usage_v1_projects__project_id__usage_get: (args:{project_id:string;start?:string | null;end?:string | null;group_by?:"model" | "provider" | "workflow" | "environment" | "day"}) => {
    const query=new URLSearchParams();
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    if(args.group_by!==undefined && args.group_by!==null) query.set("group_by",String(args.group_by));
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
  upload_scan_v1_projects__project_id__scans_post: (args:{project_id:string;body:components["schemas"]["Inventory"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/scans`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  inventory_v1_projects__project_id__inventory_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
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
  prices_v1_projects__project_id__prices_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/prices`+(query.size?"?"+query.toString():""),{method:"GET"});},
  opportunities_v1_projects__project_id__opportunities_get: (args:{project_id:string;start?:string | null;end?:string | null}) => {
    const query=new URLSearchParams();
    if(args.start!==undefined && args.start!==null) query.set("start",String(args.start));
    if(args.end!==undefined && args.end!==null) query.set("end",String(args.end));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/opportunities`+(query.size?"?"+query.toString():""),{method:"GET"});},
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
  create_policy_v1_projects__project_id__capture_policies_post: (args:{project_id:string;body:components["schemas"]["CapturePolicy"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/capture-policies`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  policies_v1_projects__project_id__capture_policies_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/capture-policies`+(query.size?"?"+query.toString():""),{method:"GET"});},
  revoke_policy_v1_projects__project_id__capture_policies__policy_id__delete: (args:{project_id:string;policy_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/capture-policies/${encodeURIComponent(String(args.policy_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
  import_dataset_v1_projects__project_id__datasets_post: (args:{project_id:string;body:components["schemas"]["DatasetImport"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/datasets`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  datasets_v1_projects__project_id__datasets_get: (args:{project_id:string;limit?:number;offset?:number}) => {
    const query=new URLSearchParams();
    if(args.limit!==undefined && args.limit!==null) query.set("limit",String(args.limit));
    if(args.offset!==undefined && args.offset!==null) query.set("offset",String(args.offset));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/datasets`+(query.size?"?"+query.toString():""),{method:"GET"});},
  read_plan_content_v1_projects__project_id__experiments_plans__plan_id__content_post: (args:{project_id:string;plan_id:string;body:components["schemas"]["ContentPurpose"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/plans/${encodeURIComponent(String(args.plan_id))}/content`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
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
  recommendations_v1_projects__project_id__recommendations_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/recommendations`+(query.size?"?"+query.toString():""),{method:"GET"});},
  review_v1_projects__project_id__recommendations__recommendation_id__review_post: (args:{project_id:string;recommendation_id:string;body:components["schemas"]["ReviewInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/recommendations/${encodeURIComponent(String(args.recommendation_id))}/review`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
  export_v1_projects__project_id__export_get: (args:{project_id:string}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/export`+(query.size?"?"+query.toString():""),{method:"GET"});},
  delete_project_v1_projects__project_id__delete: (args:{project_id:string;confirm_slug:string}) => {
    const query=new URLSearchParams();
    if(args.confirm_slug!==undefined && args.confirm_slug!==null) query.set("confirm_slug",String(args.confirm_slug));
    return request(`/projects/${encodeURIComponent(String(args.project_id))}`+(query.size?"?"+query.toString():""),{method:"DELETE"});},
};}
