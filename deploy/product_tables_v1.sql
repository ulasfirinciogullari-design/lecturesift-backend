-- Owner-only additive schema. The migration driver skips whole existing tables
-- only after verifying their exact contract; it never repairs partial schemas.
CREATE TABLE public.assistant_credit_grants_v1 (
    id character varying(64) NOT NULL,
    user_id character varying(36) NOT NULL,
    source_reference character varying(64) NOT NULL,
    kind character varying(16) NOT NULL,
    credits integer NOT NULL,
    remaining integer NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT assistant_credit_grants_v1_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_assistant_credit_grants_v1_user_id ON public.assistant_credit_grants_v1 (user_id);

CREATE TABLE public.assistant_credit_requests_v1 (
    id character varying(64) NOT NULL,
    user_id character varying(36) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    state character varying(16) NOT NULL,
    allocations_json text NOT NULL,
    reserved integer NOT NULL,
    charged integer NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    response_json text,
    budget_day character varying(10) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT assistant_credit_requests_v1_pkey PRIMARY KEY (id)
);

CREATE INDEX ix_assistant_credit_requests_v1_user_id ON public.assistant_credit_requests_v1 (user_id);

CREATE TABLE public.assistant_daily_budget_v1 (
    day character varying(80) NOT NULL,
    credits integer NOT NULL,
    CONSTRAINT assistant_daily_budget_v1_pkey PRIMARY KEY (day)
);

CREATE TABLE public.billing_referral_codes (
    user_id character varying(36) NOT NULL,
    code character varying(32) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT billing_referral_codes_pkey PRIMARY KEY (user_id),
    CONSTRAINT billing_referral_codes_code_key UNIQUE (code)
);

CREATE TABLE public.billing_referral_coupons (
    code character varying(32) NOT NULL,
    user_id character varying(36) NOT NULL,
    reward_id character varying(36) NOT NULL,
    status character varying(16) NOT NULL,
    percent integer NOT NULL,
    max_discount_minor integer NOT NULL,
    currency character varying(3) NOT NULL,
    order_reference character varying(64),
    discount_minor integer,
    created_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    CONSTRAINT billing_referral_coupons_pkey PRIMARY KEY (code),
    CONSTRAINT billing_referral_coupons_reward_id_key UNIQUE (reward_id),
    CONSTRAINT billing_referral_coupons_order_reference_key UNIQUE (order_reference)
);

CREATE INDEX ix_billing_referral_coupons_user_id ON public.billing_referral_coupons (user_id);

CREATE TABLE public.billing_referral_renewal_rewards (
    id character varying(36) NOT NULL,
    referral_id character varying(36) NOT NULL,
    invitee_user_id character varying(36) NOT NULL,
    inviter_user_id character varying(36) NOT NULL,
    order_reference character varying(64) NOT NULL,
    status character varying(24) NOT NULL,
    reward_choice character varying(16),
    policy_version character varying(32) NOT NULL,
    reservation_month character varying(7) NOT NULL,
    slot_month character varying(7),
    inviter_minutes integer NOT NULL,
    invitee_minutes integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    qualified_at timestamp with time zone NOT NULL,
    pending_until timestamp with time zone NOT NULL,
    released_at timestamp with time zone,
    reconciliation_reference character varying(120),
    CONSTRAINT billing_referral_renewal_rewards_pkey PRIMARY KEY (id),
    CONSTRAINT billing_referral_renewal_rewards_order_reference_key UNIQUE (order_reference),
    CONSTRAINT uq_referral_renewal_invitee_month UNIQUE (invitee_user_id, slot_month)
);

CREATE INDEX ix_billing_referral_renewal_rewards_referral_id ON public.billing_referral_renewal_rewards (referral_id);

CREATE INDEX ix_billing_referral_renewal_rewards_invitee_user_id ON public.billing_referral_renewal_rewards (invitee_user_id);

CREATE INDEX ix_billing_referral_renewal_rewards_inviter_user_id ON public.billing_referral_renewal_rewards (inviter_user_id);

CREATE TABLE public.billing_referral_reward_preferences (
    reward_id character varying(36) NOT NULL,
    coupon_currency character varying(3) NOT NULL,
    CONSTRAINT billing_referral_reward_preferences_pkey PRIMARY KEY (reward_id)
);

CREATE TABLE public.billing_referral_rewards (
    id character varying(36) NOT NULL,
    invitee_user_id character varying(36) NOT NULL,
    inviter_user_id character varying(36) NOT NULL,
    order_reference character varying(64),
    status character varying(24) NOT NULL,
    reward_choice character varying(16),
    policy_version character varying(32) NOT NULL,
    reservation_month character varying(7),
    inviter_minutes integer NOT NULL,
    invitee_minutes integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    qualified_at timestamp with time zone,
    pending_until timestamp with time zone,
    released_at timestamp with time zone,
    reconciliation_reference character varying(120),
    CONSTRAINT billing_referral_rewards_pkey PRIMARY KEY (id),
    CONSTRAINT billing_referral_rewards_invitee_user_id_key UNIQUE (invitee_user_id),
    CONSTRAINT billing_referral_rewards_order_reference_key UNIQUE (order_reference)
);

CREATE INDEX ix_billing_referral_rewards_inviter_user_id ON public.billing_referral_rewards (inviter_user_id);
