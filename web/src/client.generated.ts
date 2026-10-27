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
  usage_v1_projects__project_id__usage_get: (args:{project_id:string;start?:string | null;end?:string | null;group_by?:"model" | "provider" | "workflow" | "environment" | "day" | "call_site" | "traffic";traffic_kind?:"application" | "evaluation" | "judge" | "unknown" | null;call_site?:string | null}) => {
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
  upload_scan_v1_projects__project_id__scans_post: (args:{project_id:string;body:components["schemas"]["Inventory"]}) => {
    const query=new URLSearchParams();
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
  create_draft_v1_projects__project_id__experiments_draft_post: (args:{project_id:string;body:components["schemas"]["DraftInput"]}) => {
    const query=new URLSearchParams();
    return request(`/projects/${encodeURIComponent(String(args.project_id))}/experiments/draft`+(query.size?"?"+query.toString():""),{method:"POST",body:JSON.stringify(args.body)});},
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
};}
