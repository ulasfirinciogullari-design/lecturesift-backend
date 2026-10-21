-- Explicit additive study-pack feedback release. No runtime DDL.
CREATE TABLE public.lecturesift_job_feedback (
  id character varying(36) NOT NULL PRIMARY KEY,
  job_id character varying(80) NOT NULL UNIQUE,
  user_id character varying(36),
  rating character varying(8) NOT NULL,
  comment character varying(280),
  consent boolean NOT NULL,
  locale character varying(8) NOT NULL,
  created_at timestamp with time zone NOT NULL,
  updated_at timestamp with time zone NOT NULL
);
CREATE INDEX ix_lecturesift_job_feedback_user_id ON public.lecturesift_job_feedback (user_id);
