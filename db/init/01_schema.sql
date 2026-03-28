-- ============================================================
-- KALO — Kaloric Assistant
-- Schema v1.0
-- Timezone: America/Bogota (aplicado en docker-compose)
-- ============================================================

SET timezone = 'America/Bogota';

-- ── Extensiones ─────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Enums ───────────────────────────────────────────────────
CREATE TYPE fuente_caloria AS ENUM ('MANUAL', 'FOTO_LLM');
CREATE TYPE sexo_tipo      AS ENUM ('M', 'F', 'OTRO');

-- ============================================================
-- usuarios
-- Registro passwordless. Vinculación opcional a Telegram.
-- ============================================================
CREATE TABLE IF NOT EXISTS usuarios (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT        NOT NULL UNIQUE,
    nombre              TEXT,
    telegram_id         BIGINT      UNIQUE,
    telegram_username   TEXT,
    activo              BOOLEAN     NOT NULL DEFAULT TRUE,
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_usuarios_telegram_id ON usuarios(telegram_id) WHERE telegram_id IS NOT NULL;
CREATE INDEX idx_usuarios_email       ON usuarios(email);

-- ============================================================
-- codigos_otp
-- Un código por solicitud. TTL de 10 min. Sólo un uso.
-- ============================================================
CREATE TABLE IF NOT EXISTS codigos_otp (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    codigo          TEXT        NOT NULL,
    expira_en       TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '10 minutes'),
    usado           BOOLEAN     NOT NULL DEFAULT FALSE,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_otp_usuario ON codigos_otp(usuario_id);
CREATE INDEX idx_otp_codigo  ON codigos_otp(codigo) WHERE NOT usado;

-- ============================================================
-- perfiles
-- Uno por usuario. Guarda datos físicos y el BMR calculado.
-- BMR se recalcula automáticamente cuando se actualiza estatura/peso/edad.
-- Harris-Benedict revisado:
--   Hombre: 88.362 + (13.397 × kg) + (4.799 × cm) - (5.677 × edad)
--   Mujer:  447.593 + (9.247 × kg) + (3.098 × cm) - (4.330 × edad)
-- objetivo_kcal = BMR × factor_actividad (definido por el usuario)
-- ============================================================
CREATE TABLE IF NOT EXISTS perfiles (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id          UUID        NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,
    estatura_cm         INTEGER     NOT NULL CHECK (estatura_cm BETWEEN 50 AND 300),
    peso_kg             NUMERIC(5,2) NOT NULL CHECK (peso_kg BETWEEN 20 AND 500),
    sexo                sexo_tipo   NOT NULL,
    edad                INTEGER     NOT NULL CHECK (edad BETWEEN 5 AND 120),
    bmr                 NUMERIC(8,2) NOT NULL,          -- kcal/día en reposo calculadas
    factor_actividad    NUMERIC(4,2) NOT NULL DEFAULT 1.2, -- sedentario por defecto
    objetivo_kcal       NUMERIC(8,2) NOT NULL,          -- bmr × factor
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- registros_calorias
-- Cada comida/snack del día. La fecha es DATE para agrupar por día.
-- El timestamp completo (registrado_en) permite saber a qué hora se comió.
-- foto_path: ruta relativa si la entrada vino de una imagen.
-- ============================================================
CREATE TABLE IF NOT EXISTS registros_calorias (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID         NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fecha           DATE         NOT NULL DEFAULT CURRENT_DATE,     -- día de consumo
    registrado_en   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),            -- momento exacto del registro
    descripcion     TEXT         NOT NULL,
    kcal            NUMERIC(8,2) NOT NULL CHECK (kcal >= 0),
    fuente          fuente_caloria NOT NULL DEFAULT 'MANUAL',
    foto_path       TEXT,                                           -- sólo si fuente = FOTO_LLM
    nota            TEXT                                            -- observación libre del usuario
);

CREATE INDEX idx_cal_usuario_fecha ON registros_calorias(usuario_id, fecha);
CREATE INDEX idx_cal_fecha         ON registros_calorias(fecha);

-- ============================================================
-- registros_ejercicio
-- Cualquier actividad física. fecha = día que se realizó.
-- kcal_quemadas: el usuario las informa o las estima.
-- ============================================================
CREATE TABLE IF NOT EXISTS registros_ejercicio (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID         NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fecha           DATE         NOT NULL DEFAULT CURRENT_DATE,     -- día del ejercicio
    registrado_en   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),            -- momento del registro
    descripcion     TEXT         NOT NULL,
    duracion_min    INTEGER      CHECK (duracion_min > 0),          -- opcional
    kcal_quemadas   NUMERIC(8,2) NOT NULL CHECK (kcal_quemadas >= 0),
    nota            TEXT
);

CREATE INDEX idx_eje_usuario_fecha ON registros_ejercicio(usuario_id, fecha);
CREATE INDEX idx_eje_fecha         ON registros_ejercicio(fecha);

-- ============================================================
-- resumenes_diarios
-- Un registro por (usuario, fecha). Se upserta cada vez que
-- el usuario registra una comida o ejercicio.
-- Permite consulta O(1) del balance del día sin sumar en tiempo real.
-- ============================================================
CREATE TABLE IF NOT EXISTS resumenes_diarios (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id          UUID         NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    fecha               DATE         NOT NULL DEFAULT CURRENT_DATE,
    kcal_consumidas     NUMERIC(8,2) NOT NULL DEFAULT 0,
    kcal_quemadas       NUMERIC(8,2) NOT NULL DEFAULT 0,
    kcal_objetivo       NUMERIC(8,2) NOT NULL DEFAULT 0,    -- snapshot del objetivo al día
    kcal_disponibles    NUMERIC(8,2)
        GENERATED ALWAYS AS (kcal_objetivo - kcal_consumidas + kcal_quemadas) STORED,
    primera_entrada_en  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    actualizado_en      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_resumen_usuario_fecha UNIQUE (usuario_id, fecha)
);

CREATE INDEX idx_res_usuario_fecha ON resumenes_diarios(usuario_id, fecha DESC);

-- ============================================================
-- Triggers — actualizado_en automático
-- ============================================================
CREATE OR REPLACE FUNCTION set_actualizado_en()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.actualizado_en = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_usuarios_upd
    BEFORE UPDATE ON usuarios
    FOR EACH ROW EXECUTE FUNCTION set_actualizado_en();

CREATE TRIGGER trg_perfiles_upd
    BEFORE UPDATE ON perfiles
    FOR EACH ROW EXECUTE FUNCTION set_actualizado_en();

CREATE TRIGGER trg_resumenes_upd
    BEFORE UPDATE ON resumenes_diarios
    FOR EACH ROW EXECUTE FUNCTION set_actualizado_en();

-- ============================================================
-- Función de upsert del resumen diario
-- Se llama desde la API cada vez que se inserta/borra
-- un registro de calorias o ejercicio.
-- ============================================================
CREATE OR REPLACE FUNCTION upsert_resumen_diario(
    p_usuario_id    UUID,
    p_fecha         DATE,
    p_objetivo_kcal NUMERIC
)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_consumidas NUMERIC;
    v_quemadas   NUMERIC;
BEGIN
    SELECT COALESCE(SUM(kcal), 0)
        INTO v_consumidas
        FROM registros_calorias
        WHERE usuario_id = p_usuario_id AND fecha = p_fecha;

    SELECT COALESCE(SUM(kcal_quemadas), 0)
        INTO v_quemadas
        FROM registros_ejercicio
        WHERE usuario_id = p_usuario_id AND fecha = p_fecha;

    INSERT INTO resumenes_diarios
        (usuario_id, fecha, kcal_consumidas, kcal_quemadas, kcal_objetivo, primera_entrada_en, actualizado_en)
    VALUES
        (p_usuario_id, p_fecha, v_consumidas, v_quemadas, p_objetivo_kcal, NOW(), NOW())
    ON CONFLICT (usuario_id, fecha) DO UPDATE SET
        kcal_consumidas = EXCLUDED.kcal_consumidas,
        kcal_quemadas   = EXCLUDED.kcal_quemadas,
        kcal_objetivo   = EXCLUDED.kcal_objetivo,
        actualizado_en  = NOW();
END;
$$;
