import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import { Api, type Station } from '../api'
import { Icon } from './Icon'

/**
 * 역 검색 + 선택 (봇과 동일하게 검색 결과에서 선택해야 확정 → 오타 방지)
 */
export function StationInput({
  label,
  value,
  onChange,
  recent,
  exclude,
}: {
  label: string
  value: string
  onChange: (station: string) => void
  recent: string[]
  exclude?: string
}) {
  const id = useId()
  const [text, setText] = useState(value)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState<Station[]>([])

  // 부모에서 값이 바뀐 경우 (출발/도착 교체, 재예약)
  useEffect(() => setText(value), [value])

  // 입력 디바운스
  useEffect(() => {
    const trimmed = text.trim()
    if (!trimmed || trimmed === value) {
      setQuery('')
      return
    }
    const timer = setTimeout(() => {
      setQuery(trimmed)
      setPage(1)
    }, 300)
    return () => clearTimeout(timer)
  }, [text, value])

  const search = useQuery({
    queryKey: ['stations', query, page],
    queryFn: () => Api.stations(query, page),
    enabled: query.length > 0,
    staleTime: 60 * 60 * 1000,
  })

  useEffect(() => {
    if (!search.data) return
    setResults((prev) => (page === 1 ? search.data.stations : [...prev, ...search.data.stations]))
  }, [search.data, page])

  const select = (name: string) => {
    onChange(name)
    setText(name)
    setQuery('')
    setOpen(false)
  }

  const hasMore = search.data ? page * search.data.page_size < search.data.total : false
  const confirmed = value !== '' && text === value
  const recentOptions = recent.filter((name) => name !== exclude && name !== value)

  return (
    <div className="field station-field">
      <label htmlFor={id}>{label}</label>
      <div className={`input-with-icon${confirmed ? ' confirmed' : ''}`}>
        <Icon name={confirmed ? 'check' : 'search'} size={18} />
        <input
          id={id}
          value={text}
          autoComplete="off"
          placeholder="역 이름 검색 (예: 서울, 광주송정)"
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
            if (value) onChange('')
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          aria-autocomplete="list"
          aria-expanded={open && results.length > 0}
        />
      </div>

      {open && query && (
        <div className="suggestions" role="listbox">
          {search.isFetching && page === 1 && <div className="suggestion-empty">검색 중…</div>}
          {search.isError && <div className="suggestion-empty">{(search.error as Error).message}</div>}
          {!search.isFetching && search.data && results.length === 0 && (
            <div className="suggestion-empty">'{query}' 검색 결과가 없습니다</div>
          )}
          {results.map((station) => (
            <button
              type="button"
              role="option"
              aria-selected={false}
              key={`${station.code}-${station.name}`}
              className="suggestion"
              disabled={station.name === exclude}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => select(station.name)}
            >
              {station.name}
            </button>
          ))}
          {hasMore && (
            <button
              type="button"
              className="suggestion more"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setPage((p) => p + 1)}
            >
              {search.isFetching ? '불러오는 중…' : '더 보기'}
            </button>
          )}
        </div>
      )}

      {!confirmed && !query && recentOptions.length > 0 && (
        <div className="chips">
          {recentOptions.map((name) => (
            <button type="button" key={name} className="chip" onClick={() => select(name)}>
              {name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
