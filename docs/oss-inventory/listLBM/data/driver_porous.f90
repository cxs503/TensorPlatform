! m8285 driver 2: sparse-list saving measurement (solid-node elimination) + wall-clock comparison
program porous
    use imgproc2d_mod
    use image2d_mod
    use stream_mod
    use lattutil_mod
    use partppot_mod
    use ppot_mod
    use initfield_mod
    use readsolid2d_mod
    implicit none
    type(partppot2d_t),     target  :: part
    type(imageobj2d_t),     target  :: imgobj
    type(imgproc2d_t)               :: imgpro
    type(solidconstruct2d_t)        :: solid
    type(colBGKpp_t)                :: col
    type(colBGKppwall_t)            :: colw
    type(colBGKppgf_t)               :: colgf
    type(initfielduniform_t)        :: inituni
    integer, parameter :: xlen=128, ylen=128
    integer :: ic, nsteps, withboxes, n, i, j
    real(kind=wp) :: t1,t2
    character(len=32) :: arg
    call get_command_argument(1,arg); read(arg,*) withboxes
    call get_command_argument(2,arg); read(arg,*) nsteps
    call initD2Q9lattice()
    call imgobj%init(dims=(/xlen,ylen/))
    if (withboxes.eq.1) then
        call solid%init(imgobj)
        do j = 30, 90, 60
            do i = 30, 90, 60
                call solid%box(p1xyz=(/i,j/),p2xyz=(/i+25,j+25/))
            enddo
        enddo
    endif
    call imgpro%init(imageobj=imgobj,part=part)
    call imgpro%setpbc (axis=(/.true.,.true./))
    call imgpro%run()
    call imgpro%destruct()
    call imgobj%destruct()
    block
        type(potparam_t) :: pm
        pm%getpot => pot_SC
        pm%G      = -1.5_wp
        pm%rho_0  = 1._wp
        call col%init(tau=1._wp,potparam=pm)
        call colw%init(rho_wall=1.4_wp,potparam=pm)
        call part%setfluidcol(col)
        do n = 1, part%nslists
            select case (trim(part%slists(n)%pt%name))
            case ('wall')
                call part%slists(n)%pt%alloccollision(colw)
            case ('ghostf')
                call part%slists(n)%pt%alloccollision(colgf)
            end select
        end do
    end block
    call inituni%init(rho=1.0_wp)
    do n = 1, part%nslists
        call inituni%run(slist = part%slists(n)%pt)
    end do
    call cpu_time(t1)
    do ic = 1, nsteps
        call part%stream
        call part%collide
    end do
    call cpu_time(t2)
    write(*,'(A,I10,A,F12.4)') 'TIMING step ', nsteps, '  wallclock_s ', t2-t1
    write(*,'(A,I12)') 'MAINLIST_CELLS ', part%main%ind-part%main%ist+1
    do n = 1, part%nslists
        write(*,'(A,A,A,I8)') 'LIST,', trim(part%slists(n)%pt%name), ',', part%slists(n)%pt%getnumocells()
    end do
end program porous
