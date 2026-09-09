-- Explicit additive workspace/admin release. No runtime DDL.
CREATE TABLE public.workspace_folders_v1 (
  id character varying(36) NOT NULL PRIMARY KEY,
  user_id character varying(36) NOT NULL,
  name character varying(80) NOT NULL,
  created_at timestamp with time zone NOT NULL
);
CREATE INDEX ix_workspace_folders_v1_user_id ON public.workspace_folders_v1 (user_id);
CREATE TABLE public.workspace_items_v1 (
  job_id character varying(64) NOT NULL PRIMARY KEY,
  user_id character varying(36) NOT NULL,
  folder_id character varying(36)
);
CREATE INDEX ix_workspace_items_v1_user_id ON public.workspace_items_v1 (user_id);
CREATE INDEX ix_workspace_items_v1_folder_id ON public.workspace_items_v1 (folder_id);
CREATE TABLE public.admin_ad_free_grants_v1 (
  user_id character varying(36) NOT NULL PRIMARY KEY,
  enabled boolean NOT NULL,
  updated_at timestamp with time zone NOT NULL
);
CREATE TABLE public.admin_order_archives_v1 (
  reference character varying(64) NOT NULL PRIMARY KEY,
  user_id character varying(36) NOT NULL,
  created_at timestamp with time zone NOT NULL
);
CREATE INDEX ix_admin_order_archives_v1_user_id ON public.admin_order_archives_v1 (user_id);
